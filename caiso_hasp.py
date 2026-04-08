"""
CAISO HASP LMP Pricing Dashboard - Yesterday HASP + Today HASP
===============================================================
Fetches HASP 15-min LMP for ELAP_PACE-APND.
Single /data endpoint fetches yesterday then today sequentially.
One worker handles one long request, caches result, returns instantly after.

Requirements: requests, flask, gunicorn
Start: gunicorn caiso_hasp:app --timeout 300
"""

import io
import re
import sys
import time
import zipfile
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify

OASIS_URL = "https://oasis.caiso.com/oasisapi/SingleZip"
NODE      = "ELAP_PACE-APND"
VERSION   = "1"
TZ_PT     = ZoneInfo("America/Los_Angeles")
TZ_UTC    = ZoneInfo("UTC")

app = Flask(__name__)

_cache = {"data": None, "fetching": False}

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


def fetch_hour(hr, date_pt):
    start_pt  = date_pt + timedelta(hours=hr)
    end_pt    = start_pt + timedelta(hours=1)
    start_utc = start_pt.astimezone(TZ_UTC)
    end_utc   = end_pt.astimezone(TZ_UTC)

    params = {
        "queryname":     "PRC_HASP_LMP",
        "market_run_id": "HASP",
        "grp_type":      "ALL_APNODES",
        "node":          NODE,
        "startdatetime": start_utc.strftime("%Y%m%dT%H:%M-0000"),
        "enddatetime":   end_utc.strftime("%Y%m%dT%H:%M-0000"),
        "version":       VERSION,
        "resultformat":  "6",
    }

    resp = requests.get(OASIS_URL, params=params, timeout=60)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for name in zf.namelist():
            with zf.open(name) as f:
                raw = f.read()
            if raw.strip().startswith(b"<"):
                text = raw.decode("utf-8", errors="replace")
                err  = re.search(r"<m:ERR_DESC>(.*?)</m:ERR_DESC>", text)
                raise ValueError(err.group(1) if err else "CAISO XML error")
            lines = raw.decode("utf-8").strip().split("\n")
            hdr   = [h.strip().strip('"') for h in lines[0].split(",")]
            rows  = []
            for line in lines[1:]:
                vals = line.split(",")
                obj  = {hdr[i]: vals[i].strip().strip('"') for i in range(len(hdr))}
                if obj.get("NODE") == NODE and obj.get("LMP_TYPE") == "LMP":
                    rows.append(obj)
            return rows
    return []


def fetch_hours(label, date_pt, num_hours):
    all_rows = []
    print("Fetching " + label + " (" + str(num_hours) + " hours)...", flush=True)
    for hr in range(num_hours):
        try:
            rows = fetch_hour(hr, date_pt)
            all_rows.extend(rows)
            print("  " + label + " Hour " + str(hr) + ": " + str(len(rows)) + " rows", flush=True)
        except Exception as e:
            print("  " + label + " Hour " + str(hr) + " SKIPPED: " + str(e), flush=True)
        time.sleep(5)
    print(label + " done: " + str(len(all_rows)) + " rows", flush=True)
    return all_rows


@app.route("/refresh")
def refresh():
    _cache["data"]     = None
    _cache["fetching"] = False
    return jsonify({"ok": True})


@app.route("/data")
def data():
    if _cache["data"] is not None:
        return jsonify(_cache["data"])

    if _cache["fetching"]:
        return jsonify({"error": "still_loading"}), 503

    _cache["fetching"] = True
    try:
        now_pt      = datetime.now(tz=TZ_PT)
        yesterday   = (now_pt - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        today_pt    = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        hours_today = now_pt.hour

        yest_rows  = fetch_hours("Yesterday", yesterday, 24)
        today_rows = fetch_hours("Today", today_pt, hours_today)

        result = {"yesterday": yest_rows, "today": today_rows}
        _cache["data"]     = result
        _cache["fetching"] = False
        return jsonify(result)
    except Exception:
        _cache["fetching"] = False
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        return jsonify({"error": "fetch_failed"}), 500


@app.route("/")
def dashboard():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CAISO HASP LMP | ELAP_PACE-APND</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: Arial, sans-serif; background: #f0f4f8; color: #222; }
  .header {
    background: #7B68B5; color: #fff; padding: 16px 24px;
    display: flex; align-items: center; justify-content: space-between;
    margin-top: 24px;
  }
  .header:first-of-type { margin-top: 0; }
  .header h1 { font-size: 18px; }
  .header .meta { font-size: 12px; opacity: .75; text-align: right; }
  .cards { display: flex; gap: 16px; padding: 20px 24px; flex-wrap: wrap; }
  .card { background: #fff; border-radius: 8px; padding: 16px 20px; flex: 1; min-width: 140px; box-shadow: 0 1px 4px rgba(0,0,0,.1); }
  .card .label { font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: .5px; }
  .card .value { font-size: 26px; font-weight: bold; margin-top: 4px; }
  .card .value.pos { color: #1a6b2f; }
  .card .value.neg { color: #b91c1c; }
  .chart-wrap { padding: 0 24px 20px; }
  canvas { background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.1); width: 100% !important; }
  .table-wrap { padding: 0 24px 24px; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.1); font-size: 13px; }
  thead tr { background: #7B68B5; color: #fff; }
  th, td { padding: 8px 14px; text-align: center; border-bottom: 1px solid #e5e7eb; }
  tbody tr:nth-child(even) { background: #EDE9F7; }
  tbody tr:hover { background: #D8D0F0; }
  td.neg { color: #b91c1c; font-weight: bold; }
  td.pos { color: #1a6b2f; }
  .status { text-align: center; padding: 60px; color: #666; font-size: 15px; }
  .spinner { display: inline-block; width: 24px; height: 24px; border: 3px solid #ccc; border-top-color: #7B68B5; border-radius: 50%; animation: spin .8s linear infinite; margin-right: 10px; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .divider { height: 8px; background: #7B68B5; opacity: 0.3; margin-top: 24px; }
</style>
</head>
<body>

<!-- Today HASP -->
<div class="header">
  <div>
    <h1>HASP 15-Min LMP - Today So Far | ELAP_PACE-APND</h1>
    <div class="meta" id="today-subtitle">Loading...</div>
  </div>
  <div><button onclick="reload()" style="background:#fff;color:#7B68B5;border:none;padding:7px 14px;border-radius:6px;cursor:pointer;font-weight:bold;font-size:13px;">Refresh</button></div>
</div>
<div class="cards">
  <div class="card"><div class="label">Latest</div><div class="value" id="today-cLatest">-</div></div>
  <div class="card"><div class="label">High</div><div class="value pos" id="today-cHigh">-</div></div>
  <div class="card"><div class="label">Low</div><div class="value neg" id="today-cLow">-</div></div>
  <div class="card"><div class="label">Avg</div><div class="value" id="today-cAvg">-</div></div>
  <div class="card"><div class="label">Hours</div><div class="value" id="today-cHours">-</div></div>
</div>
<div class="chart-wrap"><canvas id="today-chart" height="180"></canvas></div>
<div class="table-wrap"><div id="today-table"><div class="status"><span class="spinner"></span> Fetching HASP data from CAISO... please wait (~3 min on first load).</div></div></div>

<div class="divider"></div>

<!-- Yesterday HASP -->
<div class="header">
  <div>
    <h1>HASP 15-Min LMP - Yesterday | ELAP_PACE-APND</h1>
    <div class="meta" id="yesterday-subtitle">Loading...</div>
  </div>
</div>
<div class="cards">
  <div class="card"><div class="label">High</div><div class="value pos" id="yesterday-cHigh">-</div></div>
  <div class="card"><div class="label">Low</div><div class="value neg" id="yesterday-cLow">-</div></div>
  <div class="card"><div class="label">Avg</div><div class="value" id="yesterday-cAvg">-</div></div>
  <div class="card"><div class="label">On-Peak Avg</div><div class="value" id="yesterday-cOnPeak">-</div></div>
  <div class="card"><div class="label">Off-Peak Avg</div><div class="value" id="yesterday-cOffPeak">-</div></div>
</div>
<div class="chart-wrap"><canvas id="yesterday-chart" height="180"></canvas></div>
<div class="table-wrap"><div id="yesterday-table"><div class="status"><span class="spinner"></span> Waiting for today's data to finish first...</div></div></div>

<script>
var charts    = {};
var pollTimer = null;

function nowPT() {
  return new Date(new Date().toLocaleString("en-US", {timeZone:"America/Los_Angeles"}));
}

function ensureChart(cb) {
  if (window.Chart) { cb(); return; }
  var s = document.createElement("script");
  s.src = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js";
  s.onload = cb;
  document.head.appendChild(s);
}

function renderSection(prefix, rows, isToday) {
  if (!rows || !rows.length) {
    document.getElementById(prefix + "-table").innerHTML = '<div class="status">No data returned.</div>';
    return;
  }

  var sorted = rows.map(function(r) {
    return {
      time:   r["INTERVALSTARTTIME_GMT"],
      hr:     parseFloat(r["OPR_HR"]),
      lmp:    parseFloat(r["MW"]),
      timePT: new Date(r["INTERVALSTARTTIME_GMT"]).toLocaleTimeString("en-US",
                {hour:"2-digit", minute:"2-digit", timeZone:"America/Los_Angeles", hour12:false})
    };
  }).sort(function(a,b) { return a.time < b.time ? -1 : 1; });

  var lmps    = sorted.map(function(r) { return r.lmp; });
  var onPeak  = sorted.filter(function(r) { return r.hr >= 7 && r.hr <= 22; }).map(function(r) { return r.lmp; });
  var offPeak = sorted.filter(function(r) { return r.hr < 7 || r.hr > 22; }).map(function(r) { return r.lmp; });
  var sum     = function(a) { return a.reduce(function(x,y){return x+y;},0); };

  function setCard(id, v) {
    var el = document.getElementById(id);
    el.textContent = "$" + v.toFixed(2);
    el.className = "value " + (v >= 0 ? "pos" : "neg");
  }

  setCard(prefix + "-cHigh", Math.max.apply(null, lmps));
  setCard(prefix + "-cLow",  Math.min.apply(null, lmps));
  document.getElementById(prefix + "-cAvg").textContent = "$" + (sum(lmps)/lmps.length).toFixed(2);

  if (isToday) {
    setCard(prefix + "-cLatest", lmps[lmps.length - 1]);
    document.getElementById(prefix + "-cHours").textContent = new Set(sorted.map(function(r){return r.hr;})).size;
  } else {
    document.getElementById(prefix + "-cOnPeak").textContent  = onPeak.length  ? "$" + (sum(onPeak)/onPeak.length).toFixed(2)   : "-";
    document.getElementById(prefix + "-cOffPeak").textContent = offPeak.length ? "$" + (sum(offPeak)/offPeak.length).toFixed(2) : "-";
  }

  var now = nowPT();
  var d   = isToday ? now : new Date(now.getFullYear(), now.getMonth(), now.getDate()-1);
  document.getElementById(prefix + "-subtitle").textContent =
    d.toLocaleDateString("en-US") + " | Loaded: " + now.toLocaleTimeString("en-US", {timeZone:"America/Los_Angeles"}) + " PT";

  ensureChart(function() {
    if (charts[prefix]) charts[prefix].destroy();
    var colors = lmps.map(function(v) { return v >= 0 ? "rgba(26,107,47,0.8)" : "rgba(185,28,28,0.8)"; });
    charts[prefix] = new Chart(document.getElementById(prefix + "-chart").getContext("2d"), {
      type: "bar",
      data: { labels: sorted.map(function(r){return r.timePT;}), datasets: [{ label: "LMP ($/MWh)", data: lmps, backgroundColor: colors, borderWidth: 0 }] },
      options: {
        responsive: true,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: function(c) { return " $" + c.parsed.y.toFixed(4) + "/MWh"; } } } },
        scales: {
          x: { ticks: { maxTicksLimit: 24, font: { size: 10 } } },
          y: { ticks: { callback: function(v) { return "$" + v; } }, grid: { color: "#e5e7eb" } }
        }
      }
    });
  });

  var byHr = {};
  sorted.forEach(function(r) { if (!byHr[r.hr]) byHr[r.hr]=[]; byHr[r.hr].push(r.lmp); });
  var tbl = '<table><thead><tr><th>Oper Hour</th><th>Avg ($/MWh)</th><th>Min</th><th>Max</th></tr></thead><tbody>';
  Object.keys(byHr).sort(function(a,b){return +a-+b;}).forEach(function(h) {
    var vals = byHr[h];
    var avg  = vals.reduce(function(a,b){return a+b;},0)/vals.length;
    var min  = Math.min.apply(null,vals);
    var max  = Math.max.apply(null,vals);
    tbl += '<tr><td>'+h+'</td><td class="'+(avg<0?"neg":"pos")+'">'+avg.toFixed(4)+
           '</td><td class="'+(min<0?"neg":"")+'">'+min.toFixed(4)+
           '</td><td class="'+(max<0?"neg":"pos")+'">'+max.toFixed(4)+'</td></tr>';
  });
  tbl += '</tbody></table>';
  document.getElementById(prefix + "-table").innerHTML = tbl;
}

function reload() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  document.getElementById("today-subtitle").textContent = "Fetching...";
  document.getElementById("yesterday-subtitle").textContent = "Waiting...";
  document.getElementById("today-table").innerHTML = '<div class="status"><span class="spinner"></span> Fetching HASP data from CAISO... please wait (~3 min).</div>';
  document.getElementById("yesterday-table").innerHTML = '<div class="status"><span class="spinner"></span> Waiting for today\'s data to finish first...</div>';

  // Clear server cache first, then fetch fresh data
  fetch("/refresh")
    .then(function() { return fetch("/data"); })
    .then(function(resp) {
      return resp.json().then(function(d) { return {status: resp.status, data: d}; });
    })
    .then(function(result) {
      if (result.status === 503) {
        document.getElementById("today-subtitle").textContent = "Fetching from CAISO... checking again in 15s";
        pollTimer = setTimeout(reload, 15000);
        return;
      }
      renderSection("today",     result.data.today,     true);
      renderSection("yesterday", result.data.yesterday, false);
    })
    .catch(function(e) {
      document.getElementById("today-table").innerHTML = '<div class="status">Error: ' + e.message + ' <button onclick="reload()">Retry</button></div>';
    });
}

document.addEventListener("DOMContentLoaded", reload);
</script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765)
