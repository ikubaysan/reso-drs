from __future__ import annotations
import os
import sys
import glob
import time
from dataclasses import dataclass, field
from typing import List, Optional
import subprocess
from pathlib import Path

import pygame
import xml.etree.ElementTree as ET


# -----------------------------
# Data Models
# -----------------------------

@dataclass
class LongPoint:
    tick: int
    left: float
    right: float
    left_end: Optional[float] = None
    right_end: Optional[float] = None


@dataclass
class Step:
    start_tick: int
    end_tick: int
    left: float
    right: float
    kind: int
    long_points: List[LongPoint] = field(default_factory=list)

    def is_hold(self) -> bool:
        return self.end_tick > self.start_tick


@dataclass
class Chart:
    name: str
    ticks_per_beat: int
    bpm: float
    steps: List[Step]

    def tick_to_seconds(self, tick: int) -> float:
        beats = tick / self.ticks_per_beat
        return beats * (60.0 / self.bpm)



class AudioConverter:

    @staticmethod
    def m4a_to_ogg(m4a_path: str, overwrite: bool = False) -> str:
        m4a = Path(m4a_path)
        ogg = m4a.with_suffix(".ogg")

        if ogg.exists() and not overwrite:
            print(f"[Audio] Using cached OGG: {ogg}")
            return str(ogg)

        print(f"[Audio] Converting: {m4a} -> {ogg}")

        try:
            subprocess.run([
                "ffmpeg",
                "-y" if overwrite else "-n",
                "-i", str(m4a),
                "-c:a", "libvorbis",
                "-q:a", "5",
                str(ogg)
            ], check=True)
        except subprocess.CalledProcessError:
            print("[Audio] FFmpeg conversion failed, falling back to M4A")
            return str(m4a)

        return str(ogg)



# -----------------------------
# Parser
# -----------------------------

class DRSParser:
    SCALE = 65536.0

    def parse(self, path: str) -> Chart:
        tree = ET.parse(path)
        root = tree.getroot()

        time_unit = int(root.find("./info/time_unit").text)
        bpm_raw = int(root.find("./info/bpm_info/bpm/bpm").text)
        bpm = bpm_raw / 100.0

        steps: List[Step] = []

        for s in root.findall("./sequence_data/step"):
            start = int(s.find("start_tick").text)
            end = int(s.find("end_tick").text)
            left = int(s.find("left_pos").text) / self.SCALE
            right = int(s.find("right_pos").text) / self.SCALE
            kind = int(s.find("kind").text)

            long_points: List[LongPoint] = []
            lp_node = s.find("long_point")
            if lp_node is not None:
                for p in lp_node.findall("point"):
                    tick = int(p.find("tick").text)
                    l = int(p.find("left_pos").text) / self.SCALE
                    r = int(p.find("right_pos").text) / self.SCALE

                    left_end = (
                        int(p.find("left_end_pos").text) / self.SCALE
                        if p.find("left_end_pos") is not None else None
                    )
                    right_end = (
                        int(p.find("right_end_pos").text) / self.SCALE
                        if p.find("right_end_pos") is not None else None
                    )

                    long_points.append(LongPoint(tick, l, r, left_end, right_end))

            steps.append(Step(start, end, left, right, kind, long_points))

        return Chart(os.path.basename(path), time_unit, bpm, steps)


# -----------------------------
# Folder Loader
# -----------------------------

@dataclass
class SongBundle:
    audio_path: str
    charts: List[Chart]


class FolderLoader:

    def load(self, folder: str) -> SongBundle:
        xml_files = glob.glob(os.path.join(folder, "*.xml"))
        ogg_files = glob.glob(os.path.join(folder, "*.ogg"))
        m4a_files = glob.glob(os.path.join(folder, "*.m4a"))

        if not xml_files:
            raise RuntimeError("No XML charts found")

        # Prefer OGG
        if ogg_files:
            audio_path = ogg_files[0]
            print(f"[Audio] Found OGG: {audio_path}")
        elif m4a_files:
            print(f"[Audio] No OGG found, converting M4A...")
            audio_path = AudioConverter.m4a_to_ogg(m4a_files[0])
        else:
            raise RuntimeError("No audio file found (.ogg or .m4a)")

        parser = DRSParser()
        charts = [parser.parse(x) for x in xml_files]

        return SongBundle(audio_path, charts)


# -----------------------------
# Renderer
# -----------------------------

class Player:
    STEP_STYLE = {
        1: {"color": (255, 140, 0), "label": "L"},  # Orange
        2: {"color": (0, 120, 255), "label": "R"},  # Blue
        3: {"color": (180, 80, 255), "label": "D"},  # Purple (Down)
        4: {"color": (255, 60, 60), "label": "J"},  # Red (Jump)
    }

    def __init__(self, bundle: SongBundle):
        self.bundle = bundle
        self.width = 1200
        self.height = 800
        self.scroll_speed = 300  # pixels per second


    def run(self) -> None:
        pygame.init()
        pygame.mixer.init()

        screen = pygame.display.set_mode((self.width, self.height))
        clock = pygame.time.Clock()

        pygame.mixer.music.load(self.bundle.audio_path)
        pygame.mixer.music.play()

        start_time = time.time()

        running = True
        while running:
            now = time.time() - start_time

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            screen.fill((0, 0, 0))

            self.draw_charts(screen, now)

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()

    def draw_charts(self, screen, current_time: float):
        chart_count = len(self.bundle.charts)
        chart_width = self.width // chart_count

        for i, chart in enumerate(self.bundle.charts):
            x_offset = i * chart_width
            self.draw_chart(screen, chart, x_offset, chart_width, current_time)

        # 🔹 Draw vertical dividers
        for i in range(1, chart_count):
            x = i * chart_width
            pygame.draw.line(
                screen,
                (80, 80, 80),  # divider color (subtle gray)
                (x, 0),
                (x, self.height),
                2  # thickness
            )

    def draw_chart(self, screen, chart: Chart, x_offset: int, width: int, now: float):
        font = pygame.font.SysFont(None, 18)

        for step in chart.steps:
            start_t = chart.tick_to_seconds(step.start_tick)
            end_t = chart.tick_to_seconds(step.end_tick)

            y = self.height - (start_t - now) * self.scroll_speed

            if y < -50 or y > self.height + 50:
                continue

            style = self.STEP_STYLE.get(step.kind, {"color": (255, 255, 255), "label": "?"})
            color = style["color"]
            label = style["label"]

            left_px = x_offset + int(step.left * width)
            right_px = x_offset + int(step.right * width)

            center_x = (left_px + right_px) // 2

            # ------------------------
            # TAP
            # ------------------------
            if not step.is_hold():
                pygame.draw.rect(screen, color, (left_px, y, right_px - left_px, 10))

                text = font.render(label, True, (255, 255, 255))
                screen.blit(text, (center_x - 5, y - 10))
                continue

            # ------------------------
            # HOLD / SLIDE
            # ------------------------
            end_y = self.height - (end_t - now) * self.scroll_speed

            pygame.draw.rect(screen, color, (left_px, end_y, right_px - left_px, y - end_y))

            text = font.render(label, True, (255, 255, 255))
            screen.blit(text, (center_x - 5, y - 10))

# -----------------------------
# Main
# -----------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python player.py <folder>")
        sys.exit(1)

    folder = sys.argv[1]

    loader = FolderLoader()
    bundle = loader.load(folder)

    player = Player(bundle)
    player.run()