"""One wind slide per RI NOAA station, in the RICHAMP event-deck style.

Draws each station's figure with wind_figure.py and places it on a slide built with the
richamp-event-presentation skill's common.py (frame, title, picture box 11.2 x 5.5 in).
The skill lives in .claude/skills/ on this PC (not in git), so this script needs it there.

Usage: python make_wind_slides.py <wind_csv> "<cycle label, e.g. GFS Nov 24 12Z>" <out.pptx>
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent.parent / ".claude" / "skills" / "richamp-event-presentation" / "scripts"
if not (SHARED / "common.py").exists():
    sys.exit(f"needs the richamp-event-presentation skill at {SHARED}")
if len(sys.argv) != 4:
    sys.exit(__doc__)
CSV, CYCLE, OUT = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])

exec(open(SHARED / "common.py", encoding="utf-8").read())
BACKGROUND = SHARED.parent / "assets" / "background.png"
FOOTER = "RICHAMP Storm Surge Forecast"

STATIONS = [("Providence, RI", "Providence", "NOAA 8454000"),
            ("Conimicut Light, RI", "Conimicut Light", "NOAA 8452944"),
            ("Quonset Pt, RI", "Quonset Point", "NOAA 8454049"),
            ("Newport, RI", "Newport", "NOAA 8452660")]
figdir = OUT.with_suffix("")
figdir.mkdir(parents=True, exist_ok=True)
for station, name, sid in STATIONS:
    png = figdir / f"{station.split(',')[0].replace(' ', '_')}_wind.png"
    subprocess.run([sys.executable, str(HERE / "wind_figure.py"), str(CSV), station, str(png)], check=True)
    s = frame("Wind")
    title(s, f"Wind: {name}", f"{sid} • {CYCLE} • 10 m wind")
    picture(s, png, 1.3, 1.25, 11.2, 5.5)
    s.notes_slide.notes_text_frame.text = (
        f"{CYCLE} 10 m wind interpolated to the station, hourly. "
        "Barbs: half feather 5 m/s, full feather 10 m/s, flag 50 m/s; calm circle under 2.5 m/s.")
prs.save(OUT)
print(OUT)
