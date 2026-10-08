from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator

DATA_FILE = Path(os.getenv("TRIPS_FILE", Path(__file__).with_name("trips.json")))
DATA_LOCK = threading.Lock()
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
        if (self.start.tzinfo is None) != (self.end.tzinfo is None):
            raise ValueError("Укажите часовой пояс для обоих времён или ни для одного")
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
    """Return the driver's local calendar day from the timestamp's own offset."""
    return datetime.fromisoformat(trip["start"]).date()


def public_trip(trip: dict) -> dict:
    return {key: value for key, value in trip.items() if key != "fingerprint"}


def canonical_trip_time(value: datetime) -> str:
    """Serialize without losing fractional seconds or the driver's UTC offset."""
    return value.isoformat()


def trip_fingerprint(trip: dict) -> str:
    data = {key: trip[key] for key in ("start", "end", "amount", "payment", "commission")}
    for key in ("start", "end"):
        value = datetime.fromisoformat(data[key])
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        data[key] = value.isoformat()
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


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
    return [public_trip(t) for t in read_trips() if trip_day(t) == day]


@app.get("/api/summary")
def summary_for_day(day: date = Query(...)):
    trips = [t for t in read_trips() if trip_day(t) == day]
    return {"day": day.isoformat(), **summarize(trips)}


@app.post("/api/trips", status_code=201)
def add_trip(payload: TripIn):
    trips = read_trips()
    candidate = payload.model_dump(mode="json")
    candidate["start"] = canonical_trip_time(payload.start)
    candidate["end"] = canonical_trip_time(payload.end)
    fingerprint = trip_fingerprint(candidate)
    candidate["id"] = payload.id or "trip-" + fingerprint

    # Serialize read-check-write so concurrent retries cannot both append.
    with DATA_LOCK:
        trips = read_trips()
        for old in trips:
            same_id = old.get("id") == candidate["id"]
            same_trip = False
            if payload.id is None:
                same_trip = old.get("fingerprint") == fingerprint
                if not same_trip and all(key in old for key in ("start", "end", "amount", "payment", "commission")):
                    same_trip = trip_fingerprint(old) == fingerprint
            if same_id or (payload.id is None and same_trip):
                old_data = {k: v for k, v in old.items() if k not in {"id", "fingerprint"}}
                new_data = {k: v for k, v in candidate.items() if k != "id"}
                if payload.id is not None and same_id and old_data != new_data:
                    raise HTTPException(409, "Этот id уже использован для другой поездки")
                return {"trip": public_trip(old), "duplicate": True}
        candidate["fingerprint"] = fingerprint
        trips.append(candidate)
        write_trips(trips)
    return {"trip": public_trip(candidate), "duplicate": False}


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
const startInput=document.querySelector('#start'),endInput=document.querySelector('#end');
const amountInput=document.querySelector('#amount'),paymentInput=document.querySelector('#payment'),commissionInput=document.querySelector('#commission');
async function load(){
  const notice=document.querySelector('#notice');
  try {
    let days=await fetch('/api/days').then(r=>r.json());
    if(!days.length){const now=new Date();days=[new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,10)];}
    let old=day.value;
    day.innerHTML=days.map(d=>`<option value="${d}">${d}</option>`).join('');
    if(days.includes(old))day.value=old;
    let d=day.value;
    let [s,ts]=await Promise.all([
      fetch('/api/summary?day='+encodeURIComponent(d)).then(r=>r.json()),
      fetch('/api/trips?day='+encodeURIComponent(d)).then(r=>r.json())
    ]);
    document.querySelector('#cards').innerHTML=`<div class="card"><div class="label">Поездок</div><div class="value">${s.trip_count}</div></div><div class="card"><div class="label">Выручка</div><div class="value">${fmt(s.revenue)}</div></div><div class="card"><div class="label">Комиссия</div><div class="value">−${fmt(s.commission)}</div></div><div class="card"><div class="label">Наличные / карта</div><div class="value" style="font-size:18px">${fmt(s.cash)} / ${fmt(s.card)}</div></div><div class="card take"><div class="label">На руки</div><div class="value">${fmt(s.take_home)}</div></div>`;
    document.querySelector('#count').textContent=ts.length+' поездок';
    document.querySelector('#rows').innerHTML=ts.length?ts.map(t=>`<article class="row"><div><strong>${t.start.slice(11,16)}–${t.end.slice(11,16)}</strong><div class="muted">${t.payment==='cash'?'Наличные':'Карта'} · комиссия ${fmt(t.commission)}</div></div><div class="money">${fmt(t.amount)}</div></article>`).join(''):'<div class="muted">За этот день поездок пока нет</div>';
    notice.textContent='';
  } catch(error) {
    notice.textContent='Не удалось загрузить данные. Проверьте соединение и обновите страницу.';
  }
}
day.onchange=load;
document.querySelector('#form').onsubmit=async e=>{
  e.preventDefault();
  const body={start:startInput.value+':00',end:endInput.value+':00',amount:+amountInput.value,payment:paymentInput.value,commission:+commissionInput.value};
  const notice=document.querySelector('#notice');
  try {
    const r=await fetch('/api/trips',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result=await r.json();
    if(!r.ok){notice.textContent=Array.isArray(result.detail)?result.detail.map(x=>x.msg).join(', '):result.detail||'Проверьте данные';return;}
    notice.textContent=result.duplicate?'Такая поездка уже добавлена':'Поездка сохранена';
    e.target.reset();
    day.value=result.trip.start.slice(0,10);
    await load();
  } catch(error) {
    notice.textContent='Не удалось сохранить поездку. Проверьте соединение и отправьте форму ещё раз.';
  }
};
load();
</script></html>'''
