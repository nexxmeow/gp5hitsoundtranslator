import sys
import os
import re
import guitarpro
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout,
    QPushButton, QLineEdit, QLabel, QFileDialog,
    QMessageBox
)

PRIORITY_ORDER = ['kick', 'snare', 'crash', 'hihat']


def note_to_type(note):
    if note.value in (35, 36):
        return 'kick'
    if note.value in (38, 39, 40):
        return 'snare'
    if note.value in (49, 52, 55, 57):
        return 'crash'
    if note.value in (37, 42, 44, 46):
        return 'hihat'
    return None


HIT_CONFIG = {
    'kick': {'x': 256, 'y': 192, 'sample': '3:1:0:0:', 'hit_sound': 0},
    'snare': {'x': 352, 'y': 192, 'sample': '1:0:0:0:', 'hit_sound': 8},
    'crash': {'x': 352, 'y': 192, 'sample': '0:0:0:0:', 'hit_sound': 4},
    'hihat': {'x': 160, 'y': 192, 'sample': '1:0:0:0:', 'hit_sound': 0},
}


def parse_osu_template(template_path):
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    offset_ms = 0
    in_timing_points = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == '[TimingPoints]':
            in_timing_points = True
            continue
        if in_timing_points:
            if stripped.startswith('[') or not stripped or stripped.startswith('//'):
                break
            parts = stripped.split(',')
            if len(parts) >= 7:
                try:
                    time_val = int(float(parts[0]))
                    uninherited = int(parts[6])
                    if uninherited == 1:
                        offset_ms = time_val
                        break
                except (ValueError, IndexError):
                    continue

    header = re.split(r'\[HitObjects\]', content, 1)[0].strip() + "\n[HitObjects]\n"
    header = re.sub(
        r'^(Version\s*:).*$',
        r'\1HS_TEST',
        header,
        flags=re.MULTILINE | re.IGNORECASE
    )
    return offset_ms, header


def find_drum_track(song):
    for track in song.tracks:
        if track.isPercussionTrack or any(kw in track.name.lower() for kw in ['drum', 'perc', 'удар', 'бар']):
            return track
    return None


def convert_gp_to_osu(gp_path, template_path, output_dir):
    offset_ms, header = parse_osu_template(template_path)
    song = guitarpro.parse(gp_path)
    drum_track = find_drum_track(song)
    if not drum_track:
        raise RuntimeError("No drum track in this tab")

    bpm = getattr(song, 'tempo', 120)
    if hasattr(song, 'tempos') and song.tempos:
        bpm = song.tempos[0].value
    tpb = 960  # CONSTANT GP5 VALUE
    ms_per_tick = 60_000.0 / bpm / tpb
    events_float = []

    for measure in drum_track.measures:
        for voice in measure.voices[:1]:
            for beat in voice.beats:
                if not beat.notes:
                    continue
                abs_ticks = int(beat.start)
                time_float = abs_ticks * ms_per_tick

                notes = [n.value for n in beat.notes]
                has_kick = any(n in (35, 36) for n in notes)
                has_snare = any(n in (38, 39, 40) for n in notes)
                has_crash = any(n in (49, 52, 55, 57) for n in notes)
                has_hihat = any(n in (37, 42, 44, 46) for n in notes)

                drum_type = None
                hit_sound = 0

                if has_crash:
                    if has_kick:
                        drum_type = 'kick'
                        hit_sound = 4
                    elif has_snare:
                        drum_type = 'snare'
                        hit_sound = 12
                    else:
                        drum_type = 'crash'
                        hit_sound = 4
                elif has_kick:
                    drum_type = 'kick'
                    hit_sound = 0
                elif has_snare:
                    drum_type = 'snare'
                    hit_sound = 8
                elif has_hihat:
                    drum_type = 'hihat'
                    hit_sound = 0

                if drum_type:
                    events_float.append((time_float, drum_type, hit_sound))

    if not events_float:
        raise RuntimeError("Drumhits not found")

    events_float.sort(key=lambda x: x[0])
    first_time = events_float[0][0]
    events = []
    for t_float, typ, hs in events_float:
        osu_time = offset_ms + (t_float - first_time)
        events.append((int(round(osu_time)), typ, hs))

    gp_name = Path(gp_path).stem
    output_path = os.path.join(output_dir, f"{gp_name}_drums.osu")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header)
        for time_ms, drum_type, hit_sound in events:
            cfg = HIT_CONFIG[drum_type]
            f.write(f"{cfg['x']},{cfg['y']},{time_ms},1,{hit_sound},{cfg['sample']}\n")

    return output_path, len(events)


# === GUI ===
class Application(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(".gp5 to Hitsound Converter")
        self.resize(650, 260)

        layout = QVBoxLayout()

        self.gp_line = QLineEdit()
        self.gp_btn = QPushButton("Select tab (.gp5 ONLY)")
        self.gp_btn.clicked.connect(lambda: self._select_file(self.gp_line, "Guitar Pro (*.gp5)"))

        self.osu_line = QLineEdit()
        self.osu_btn = QPushButton("Select .osu template")
        self.osu_btn.clicked.connect(lambda: self._select_file(self.osu_line, "osu! Beatmap (*.osu)"))

        self.out_line = QLineEdit()
        self.out_btn = QPushButton("Output directory")
        self.out_btn.clicked.connect(self._select_dir)

        layout.addWidget(QLabel("Tab file:"))
        layout.addWidget(self.gp_line)
        layout.addWidget(self.gp_btn)

        layout.addWidget(QLabel(".osu template (for offset and stuff):"))
        layout.addWidget(self.osu_line)
        layout.addWidget(self.osu_btn)

        layout.addWidget(QLabel("Save to directory:"))
        layout.addWidget(self.out_line)
        layout.addWidget(self.out_btn)

        self.go_btn = QPushButton("Create hitsound file (.osu)")
        self.go_btn.clicked.connect(self._run_conversion)
        layout.addWidget(self.go_btn)

        self.setLayout(layout)

    def _select_file(self, line_edit, filter_str):
        path, _ = QFileDialog.getOpenFileName(self, "", "", filter_str)
        if path:
            line_edit.setText(path)

    def _select_dir(self):
        path = QFileDialog.getExistingDirectory(self, "")
        if path:
            self.out_line.setText(path)

    def _run_conversion(self):
        gp = self.gp_line.text().strip()
        tpl = self.osu_line.text().strip()
        out = self.out_line.text().strip()

        if not all([gp, tpl, out]):
            QMessageBox.warning(self, "Error", "Fill each field.")
            return
        if not (os.path.isfile(gp) and os.path.isfile(tpl) and os.path.isdir(out)):
            QMessageBox.warning(self, "Error", "Check the directories.")
            return

        try:
            result_path, count = convert_gp_to_osu(gp, tpl, out)
            QMessageBox.information(self, "Converted.", f"{count} notes placed.\n{result_path}")
        except Exception as e:
            QMessageBox.critical(self, "Fatal Error", str(e))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Application()
    window.show()
    sys.exit(app.exec())
