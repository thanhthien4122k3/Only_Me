"""Kiểm tra paper trading hằng ngày. Tự sửa lỗi vận hành có giới hạn, ghi báo cáo.

Quy tắc cứng:
- KHÔNG sửa code, config.yaml, paper_config*.yaml, PREREGISTERED.md.
- KHÔNG đổi tham số, KHÔNG quét tham số, KHÔNG đặt lệnh, KHÔNG đọc API key.
- Tự sửa DUY NHẤT: khởi động lại `paper.py --loop` nếu loop đã dừng (tối đa 2 lần/ngày).
- Mọi thứ khác: chỉ báo cáo. Ghi báo cáo vào paper/daily/.

Mã thoát: 0 = OK, 1 = CẦN XEM, 2 = CẦN NGƯỜI XỬ LÝ.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJ = Path(__file__).resolve().parents[4]
SKILL = PROJ / ".claude" / "skills" / "paper-daily-check"
os.chdir(PROJ)
sys.path.insert(0, str(PROJ))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd  # noqa: E402

from data import TF_MS, load_ohlcv  # noqa: E402
from engine import run_backtest  # noqa: E402
from paper import InstanceLock, compare_trades, load_paper_cfg  # noqa: E402
from strategy import build_features  # noqa: E402

MEMORY = SKILL / "memory"
PAPER = PROJ / "paper"
DAILY = PAPER / "daily"
STORE = PAPER / "candles"
SKILL_STATE = PAPER / "skill_state.json"
PY = PROJ / ".venv" / "Scripts" / "python.exe"
PY = PY if PY.exists() else Path(sys.executable)
H1 = TF_MS["1h"]
MAX_RESTARTS_PER_DAY = 2
TRADE_TOLERANCE = 0.30  # tiêu chí bổ sung: số lệnh live trong ±30% backtest cùng cửa sổ


def load_skill_state():
    if SKILL_STATE.exists():
        return json.loads(SKILL_STATE.read_text(encoding="utf-8"))
    return {"log_lines_seen": 0, "restarts": {}}


def save_skill_state(st):
    SKILL_STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


def loop_alive():
    """Thử giữ khóa của paper: nếu giữ được -> không có loop nào đang chạy."""
    try:
        InstanceLock(PAPER).acquire().release()
    except RuntimeError:
        return True
    pid = PAPER / "paper.pid"
    if pid.exists():
        pid.unlink()  # lock không còn được giữ, PID cũ không còn hợp lệ
    return False


def new_log_lines(st):
    log = PAPER / "run.log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    seen = st.get("log_lines_seen", 0)
    if seen > len(lines):  # log bị viết lại
        seen = 0
    st["log_lines_seen"] = len(lines)
    return lines[seen:]


def last_state():
    p = PAPER / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def restart_loop(st, today):
    used = st["restarts"].get(today, 0)
    if used >= MAX_RESTARTS_PER_DAY:
        return False, f"đã tự khởi động lại {used} lần hôm nay - dừng tự sửa, cần người xem"
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = open(PAPER / "loop_stdout.log", "a", encoding="utf-8")
    subprocess.Popen([str(PY), "paper.py", "--loop"], cwd=PROJ, stdout=out,
                     stderr=subprocess.STDOUT, creationflags=flags, env=env)
    st["restarts"][today] = used + 1
    for _ in range(30):  # chờ tiến trình mới giữ khóa (import pandas mất vài giây)
        time.sleep(1)
        if loop_alive():
            return True, f"đã khởi động lại loop (lần {used + 1}/{MAX_RESTARTS_PER_DAY} hôm nay)"
    return False, "đã khởi động loop nhưng chưa thấy khóa được giữ - kiểm tra loop_stdout.log"


def run_paper(args, timeout=900):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([str(PY), "paper.py", *args], cwd=PROJ, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)


def ms_to_str(ms):
    return pd.to_datetime(ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")


def replay_section(state):
    if not STORE.exists():
        return {"ok": False, "msg": "chưa có store nến live (paper/candles)"}
    r = run_paper(["replay", "--market", "spot", "--start", ms_to_str(state["start_ts"]),
                   "--end", ms_to_str(state["last_ts"]), "--data-dir", str(STORE),
                   "--out-dir", str(DAILY / "_replay_tmp")])
    m = re.search(r"(\d+)/(\d+) lệnh khớp", r.stdout)
    if not m:
        return {"ok": False, "msg": "không đọc được kết quả replay", "raw": r.stdout + r.stderr}
    match, total = int(m.group(1)), int(m.group(2))
    return {"ok": r.returncode == 0 and match == total, "match": match, "total": total,
            "msg": f"{match}/{total} lệnh khớp"}


def merged_ohlcv(sym, tf):
    """Lịch sử đầy đủ = data/ (cũ) + store live (mới), khử trùng theo ts."""
    cols = ["timestamp", "ts", "open", "high", "low", "close", "volume"]
    parts = [load_ohlcv(sym, tf, "data")[cols]]
    path = STORE / f"{sym.replace('/', '-')}_{tf}.parquet"
    if path.exists():
        live = pd.read_parquet(path)[cols]
        parts.append(live)
    df = pd.concat(parts, ignore_index=True).drop_duplicates(subset="ts", keep="last")
    df = df.sort_values("ts").reset_index(drop=True)
    df["close_time"] = df["timestamp"] + pd.to_timedelta(TF_MS[tf], unit="ms")
    return df


def backtest_section(state):
    cfg = load_paper_cfg("paper/paper_config.yaml")
    frames = {sym: build_features(merged_ohlcv(sym, "1h"), merged_ohlcv(sym, "4h"), cfg)
              for sym in cfg["data"]["symbols"]}
    start = pd.Timestamp(state["start_ts"], unit="ms", tz="UTC")
    end = pd.Timestamp(state["last_ts"], unit="ms", tz="UTC")
    bt, *_ = run_backtest(frames, cfg, market_type="spot",
                          execution_start=start, execution_end=end)
    paper_tr = pd.read_csv(PAPER / "trades.csv", parse_dates=["entry_time", "exit_time"])
    match, total, diffs = compare_trades(paper_tr, bt)
    n_p, n_b = len(paper_tr), len(bt)
    within = abs(n_p - n_b) <= TRADE_TOLERANCE * max(n_b, 1)
    return {"paper_n": n_p, "bt_n": n_b, "match": match, "diffs": diffs[:10],
            "within_tolerance": within}


def bullets(items):
    return "\n".join(f"- {x}" for x in items) if items else "- không có"


def known_incidents():
    """Các mục INC-* trong memory\\incidents.md chưa được đánh dấu đã xử lý."""
    p = MEMORY / "incidents.md"
    if not p.exists():
        return []
    out, cur = [], None
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = [line[3:].strip(), ""]
            out.append(cur)
        elif line.startswith("- Trạng thái:") and cur is not None:
            cur[1] = line.split(":", 1)[1].strip()
    return [f"{t} - {s}" for t, s in out if "đã xử lý" not in s]


def log_incident(problem, report_path):
    """Ghi sự cố mới vào memory\\incidents.md, có bằng chứng; không ghi trùng cùng nội dung."""
    p = MEMORY / "incidents.md"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if problem in text:
        return
    rel = report_path.relative_to(PROJ)
    block = (f"\n---\n\n## AUTO-{datetime.now(timezone.utc):%Y%m%d-%H%M} {problem[:60]}\n"
             f"- Ngày: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n"
             f"- Triệu chứng: {problem}\n"
             f"- Bằng chứng: {rel}\n"
             f"- Trạng thái: chưa xử lý (skill ghi, người dùng cần đóng)\n")
    p.write_text(text + block, encoding="utf-8")


def store_status(state):
    """K-04: store phải có đủ nến đến state.last_ts (cho spot 1H)."""
    for sym in load_paper_cfg("paper/paper_config.yaml")["data"]["symbols"]:
        path = STORE / f"{sym.replace('/', '-')}_1h.parquet"
        if not path.exists():
            return False, f"store thiếu {path.name}"
        last = int(pd.read_parquet(path, columns=["ts"])["ts"].max())
        if last < state["last_ts"]:
            return False, f"store cũ: {path.name} đến {ms_to_str(last)} < state {ms_to_str(state['last_ts'])}"
    return True, "store ok"


def main():
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    st = load_skill_state()
    actions, warnings, problems = [], [], []

    alive = loop_alive()
    new_lines = new_log_lines(st)
    errs = [l for l in new_lines if "ERROR" in l or "CRITICAL" in l]
    state = last_state()
    last_closed = (int(now.timestamp() * 1000) // H1 - 1) * H1

    if not alive:
        ok, msg = restart_loop(st, today)
        actions.append(msg)
        alive = ok
        if not ok:
            problems.append("loop đã dừng và không khởi động lại được")

    lag = None
    if state is None:
        problems.append("không có paper/state.json")
    else:
        lag = (last_closed - state["last_ts"]) // H1
        if lag >= 2 and alive:
            warnings.append(f"chu kỳ trễ {lag} nến dù loop đang chạy - xem run.log")
    if errs:
        warnings.append(f"{len(errs)} dòng ERROR/CRITICAL mới trong run.log")

    report_txt = run_paper(["report"]).stdout if state else "chưa có state"

    rp, bt = None, None
    if state:
        rp = replay_section(state)
        if not rp["ok"]:
            problems.append(f"replay không khớp live: {rp['msg']}")
        bt = backtest_section(state)
        if not bt["within_tolerance"]:
            problems.append(f"số lệnh live ({bt['paper_n']}) ngoài ±30% backtest ({bt['bt_n']})")
        if bt["match"] != bt["paper_n"] or bt["paper_n"] != bt["bt_n"]:
            warnings.append(f"lệnh live khớp backtest {bt['match']}/{bt['paper_n']} (backtest {bt['bt_n']})")

    # K-02: file kết quả phải có khi đã có state (incident INC-005)
    if state:
        missing = [f for f in ("trades.csv", "equity.csv", "orders.csv") if not (PAPER / f).exists()]
        if missing:
            problems.append(f"thiếu file kết quả {missing} dù state.json đã có chu kỳ (K-02)")
    # K-04: store phải có và đến last_ts (incident INC-006)
    if state:
        ok_store, msg_store = store_status(state)
        if not ok_store:
            problems.append(f"{msg_store} (K-04)")

    # cửa sổ 1 nến: replay/backtest trivially khớp 0/0, không có ý nghĩa đối chiếu
    short = bool(state) and state["last_ts"] <= state["start_ts"]
    note_short = " (cửa sổ 1 nến: chưa đủ dữ liệu để đối chiếu)" if short else ""

    level = 2 if problems else (1 if warnings else 0)
    verdict = {0: "OK", 1: "CẦN XEM", 2: "CẦN NGƯỜI XỬ LÝ"}[level]

    DAILY.mkdir(parents=True, exist_ok=True)
    path = DAILY / f"{now:%Y-%m-%d_%H%M}.md"
    tpl = (SKILL / "assets" / "daily_report_template.md").read_text(encoding="utf-8")
    status = [
        f"- Loop đang chạy: {'có' if alive else 'KHÔNG'}",
        f"- Nến 1H đóng gần nhất: {ms_to_str(last_closed)} UTC",
        f"- state.last_ts: {ms_to_str(state['last_ts']) if state else 'n/a'} UTC"
        + (f" (trễ {lag} nến)" if lag is not None else ""),
    ]
    fill = {
        "timestamp": f"{now:%Y-%m-%d %H:%M}",
        "verdict": verdict,
        "status": "\n".join(status),
        "actions": bullets(actions),
        "warnings": bullets(warnings),
        "problems": bullets(problems),
        "known": bullets(known_incidents()),
        "errors": bullets(errs[:20]),
        "replay": f"- {rp['msg'] if rp else 'n/a'}{note_short}",
        "backtest": (f"- Lệnh live: {bt['paper_n']}, backtest: {bt['bt_n']}, khớp từng lệnh: "
                     f"{bt['match']}, trong ±30%: {'có' if bt['within_tolerance'] else 'KHÔNG'}")
                    if bt else "- n/a",
        "report": report_txt.strip(),
    }
    text = tpl
    for k, v in fill.items():
        text = text.replace("{" + k + "}", v)
    path.write_text(text, encoding="utf-8")
    for p in problems:
        log_incident(p, path)
    save_skill_state(st)

    print(f"{verdict} -> {path}")
    for a in actions:
        print("  tự sửa:", a)
    for w in warnings:
        print("  cảnh báo:", w)
    for p in problems:
        print("  cần người xử lý:", p)
    return level


if __name__ == "__main__":
    sys.exit(main())
