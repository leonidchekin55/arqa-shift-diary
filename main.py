from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator

DATA_FILE = Path(os.getenv("TRIPS_FILE", Path(__file__).with_name("trips.json")))
app = FastAPI(title="Дневник смен", version="1.0.0")


class TripIn(BaseModel):
    id: Optional[str] = None
    start: datetime
    end: datetime
    amount: int = Field(gt=0)
    payment: Literal["cash", "card"]
    commission: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end <= self.start:
            raise ValueError("Время окончания должно быть позже начала")
        return self


def read_trips() -> list[dict]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else []


def write_trips(trips: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = DATA_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(trips, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(DATA_FILE)


def trip_day(trip: dict) -> date:
    return datetime.fromisoformat(trip["start"]).date()


def summarize(trips: list[dict]) -> dict:
    revenue = sum(t["amount"] for t in trips)
    commission = sum(t["commission"] for t in trips)
    return {
        "trip_count": len(trips), "revenue": revenue, "commission": commission,
        "take_home": revenue - commission,
        "cash": sum(t["amount"] for t in trips if t["payment"] == "cash"),
        "card": sum(t["amount"] for t in trips if t["payment"] == "card"),
    }


@app.get("/api/days")
def days():
    return sorted({trip_day(t).isoformat() for t in read_trips()}, reverse=True)


@app.get("/api/trips")
def trips_for_day(day: date = Query(...)):
    return [t for t in read_trips() if trip_day(t) == day]


@app.get("/api/summary")
def summary_for_day(day: date = Query(...)):
    trips = [t for t in read_trips() if trip_day(t) == day]
    return {"day": day.isoformat(), **summarize(trips)}


@app.post("/api/trips", status_code=201)
def add_trip(payload: TripIn):
    trips = read_trips()
    candidate = payload.model_dump(mode="json")
    # Stable key from canonical payload makes identical network retries idempotent.
    import hashlib
    canonical = json.dumps({k: v for k, v in candidate.items() if k != "id"}, sort_keys=True)
    candidate["id"] = payload.id or "trip-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
    for old in trips:
        if old["id"] == candidate["id"]:
            if {k: v for k, v in old.items() if k != "id"} != {k: v for k, v in candidate.items() if k != "id"}:
                raise HTTPException(409, "Этот id уже использован для другой поездки")
            return {"trip": old, "duplicate": True}
    trips.append(candidate)
    write_trips(trips)
    return {"trip": candidate, "duplicate": False}


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


HTML = r'''<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Дневник смен</title>
<style>
:root{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#16201b;background:#f4f6f3}*{box-sizing:border-box}body{margin:0}.wrap{max-width:850px;margin:38px auto;padding:0 18px}.top{display:flex;justify-content:space-between;align-items:center;gap:16px}h1{font-size:28px;margin:0 0 6px}.sub{color:#69756e;font-size:14px}select,button,input{font:inherit;border:1px solid #d7ded9;border-radius:10px;padding:11px;background:white;color:inherit}select{min-width:150px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:24px 0}.card,.panel{background:white;border:1px solid #e6ebe7;border-radius:16px;padding:18px}.label{color:#718078;font-size:13px}.value{font-size:25px;font-weight:700;margin-top:7px}.take{background:#143f2e;color:white}.take .label{color:#c4dfd0}.rows{display:grid;gap:9px;margin-top:12px}.row{display:flex;justify-content:space-between;gap:12px;padding:14px;background:#fafbfa;border:1px solid #eef1ef;border-radius:12px}.muted{color:#718078;font-size:13px}.money{font-weight:700;text-align:right}.toolbar{display:flex;justify-content:space-between;align-items:center}.panel h2{font-size:18px;margin:0}.add{margin-top:18px}.form{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}.form button{background:#174c36;color:white;border:0;cursor:pointer}.notice{min-height:20px;color:#a2372d;font-size:13px;margin-top:8px}@media(max-width:600px){.wrap{margin:22px auto}.cards{grid-template-columns:repeat(2,1fr)}.take{grid-column:span 2}.form{grid-template-columns:1fr 1fr}.top{align-items:flex-start}}
</style>
<main class="wrap"><header class="top"><div><h1>Дневник смен</h1><div class="sub">Поездки, выручка и выплаты за день</div></div><select id="day" aria-label="Выбрать день"></select></header>
<section class="cards" id="cards"></section><section class="panel"><div class="toolbar"><h2>Поездки</h2><span class="muted" id="count"></span></div><div class="rows" id="rows"></div></section>
<section class="panel add"><h2>Добавить поездку</h2><form id="form" class="form"><input id="start" type="datetime-local" required aria-label="Начало"><input id="end" type="datetime-local" required aria-label="Окончание"><input id="amount" type="number" min="1" placeholder="Сумма, ₸" required><select id="payment"><option value="cash">Наличные</option><option value="card">Карта</option></select><input id="commission" type="number" min="0" placeholder="Комиссия, ₸" required><button>Сохранить</button></form><div id="notice" class="notice"></div></section></main>
<script>
const fmt=n=>new Intl.NumberFormat('ru-RU').format(n)+' ₸';const day=document.querySelector('#day');
async function load(){let days=await fetch('/api/days').then(r=>r.json());if(!days.length)days=[new Date().toISOString().slice(0,10)];let old=day.value;day.innerHTML=days.map(d=>`<option>${d}</option>`).join('');if(days.includes(old))day.value=old;let d=day.value;let [s,ts]=await Promise.all([fetch('/api/summary?day='+d).then(r=>r.json()),fetch('/api/trips?day='+d).then(r=>r.json())]);document.querySelector('#cards').innerHTML=`<div class="card"><div class="label">Поездок</div><div class="value">${s.trip_count}</div></div><div class="card"><div class="label">Выручка</div><div class="value">${fmt(s.revenue)}</div></div><div class="card"><div class="label">Комиссия</div><div class="value">−${fmt(s.commission)}</div></div><div class="card"><div class="label">Наличные / карта</div><div class="value" style="font-size:18px">${fmt(s.cash)} / ${fmt(s.card)}</div></div><div class="card take"><div class="label">На руки</div><div class="value">${fmt(s.take_home)}</div></div>`;document.querySelector('#count').textContent=ts.length+' поездки';document.querySelector('#rows').innerHTML=ts.length?ts.map(t=>`<article class="row"><div><strong>${t.start.slice(11,16)}–${t.end.slice(11,16)}</strong><div class="muted">${t.payment==='cash'?'Наличные':'Карта'} · комиссия ${fmt(t.commission)}</div></div><div class="money">${fmt(t.amount)}</div></article>`).join(''):'<div class="muted">За этот день поездок пока нет</div>'}
day.onchange=load;document.querySelector('#form').onsubmit=async e=>{e.preventDefault();let local=x=>new Date(x).toISOString();let body={start:local(start.value),end:local(end.value),amount:+amount.value,payment:payment.value,commission:+commission.value};let r=await fetch('/api/trips',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let n=document.querySelector('#notice');if(!r.ok){let j=await r.json();n.textContent=j.detail?.[0]?.msg||j.detail||'Проверьте данные';return}n.textContent='Поездка сохранена';e.target.reset();await load()};load();
</script></html>'''
