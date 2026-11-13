import math
import random
import tkinter as tk
from collections import defaultdict
from tkinter import scrolledtext, ttk


BACKGROUND = "#0a1123"
COLORS = ("#66dce8", "#a99aff", "#7ee2b8", "#ffb782", "#8ab7ff", "#f3a4cd")


class SlideGalaxy(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=(12, 0, 0, 0))
        self.slides = {}
        self.points = {}
        self.groups = []
        self.sources = set()
        self.selected = None
        self.yaw = -0.20
        self.center = (0, 0, 0)
        self.zoom = 1.0
        self.animation = None
        self.drag_start = None
        randomizer = random.Random(22)
        self.stars = [(randomizer.random(), randomizer.random()) for _ in range(94)]

        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="Galaksi slide", font=("Segoe UI", 17, "bold")).pack(side="left")
        ttk.Button(header, text="Tampilkan semua", command=self.overview).pack(side="right")
        self.canvas = tk.Canvas(self, background=BACKGROUND, highlightthickness=0, height=275)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.draw())
        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.orbit)
        self.canvas.bind("<ButtonRelease-1>", self.finish_drag)

        sources = ttk.Frame(self)
        sources.pack(fill="x", pady=(10, 4))
        ttk.Label(sources, text="Slide").pack(side="left", padx=(0, 8))
        self.picker = ttk.Combobox(sources, state="readonly", takefocus=True)
        self.picker.pack(side="left", fill="x", expand=True)
        self.picker.bind("<<ComboboxSelected>>", lambda _event: self.select(list(self.slides)[self.picker.current()]))
        self.detail = scrolledtext.ScrolledText(
            self, height=6, wrap="word", state="disabled", padx=10, pady=7,
            background="#edf2f9", foreground="#142238", relief="flat", font=("Segoe UI", 10),
        )
        self.detail.pack(fill="x")
        self.show_detail("Pilih deck PowerPoint untuk melihat slide.")

    def set_deck(self, deck):
        self.slides = {slide.number: slide for slide in deck.slides}
        groups = defaultdict(list)
        for index, slide in enumerate(deck.slides):
            groups[slide.section or f"Bagian {index // 7 + 1}"].append(slide.number)
        self.groups = list(groups.values())
        self.points = {}
        for group_index, numbers in enumerate(self.groups):
            angle = math.tau * group_index / len(self.groups)
            radius = 0.60 if len(self.groups) > 1 else 0
            for i, number in enumerate(numbers):
                phase = i * 2.39996
                distance = 0.22 * math.sqrt((i + 0.5) / len(numbers))
                self.points[number] = (
                    radius * math.cos(angle) + distance * math.cos(phase),
                    0.72 * radius * math.sin(angle) + distance * math.sin(phase),
                    0.16 * math.sin(phase + angle),
                    COLORS[group_index % len(COLORS)],
                )
        self.picker["values"] = [f"{slide.number:02d}  {slide.title}" for slide in deck.slides]
        self.sources.clear()
        self.overview()

    def show_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def show_sources(self, numbers):
        previous_sources = self.sources
        self.sources = set(numbers).intersection(self.slides)
        if len(self.sources) == 1:
            self.select(next(iter(self.sources)))
        elif self.sources:
            self.overview()
            self.show_detail("Rujukan jawaban\n" + "\n".join(
                f"Slide {number}  {self.slides[number].title}" for number in sorted(self.sources)
            ))
        else:
            if previous_sources:
                self.selected = None
                self.picker.set("")
                self.show_detail("Pilih node atau nomor slide untuk membaca sumbernya.")
            self.draw()

    def select(self, number):
        slide = self.slides[number]
        self.selected = number
        self.picker.current(list(self.slides).index(number))
        self.show_detail(f"Slide {number}  {slide.title}\n\n{slide.text or slide.notes}")
        self.animate(self.points[number][:3], 1.75)

    def overview(self):
        self.selected = None
        self.picker.set("")
        if self.slides:
            self.show_detail("Pilih node atau nomor slide untuk membaca sumbernya.")
        self.animate((0, 0, 0), 1.0)

    def animate(self, target, zoom):
        if self.animation is not None:
            self.after_cancel(self.animation)
        start, initial_zoom = self.center, self.zoom

        def step(frame):
            amount = 1 - (1 - frame / 14) ** 3
            self.center = tuple(a + (b - a) * amount for a, b in zip(start, target))
            self.zoom = initial_zoom + (zoom - initial_zoom) * amount
            self.draw()
            self.animation = self.after(16, step, frame + 1) if frame < 14 else None

        step(0)

    def start_drag(self, event):
        self.drag_start = (event.x, self.yaw)

    def orbit(self, event):
        if self.drag_start is not None:
            x, yaw = self.drag_start
            self.yaw = yaw + (event.x - x) * 0.01
            self.draw()

    def finish_drag(self, event):
        start, self.drag_start = self.drag_start, None
        if start is None or abs(event.x - start[0]) > 4:
            return
        for item in reversed(self.canvas.find_overlapping(event.x - 4, event.y - 4, event.x + 4, event.y + 4)):
            tags = self.canvas.gettags(item)
            if tags and tags[0].startswith("slide-"):
                self.select(int(tags[0][6:]))
                break

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        for x, y in self.stars:
            canvas.create_oval(x * width, y * height, x * width + 1, y * height + 1, fill="#344566", outline="")
        if not self.slides:
            canvas.create_text(width / 2, height / 2, text="Pilih deck PowerPoint", fill="#c1cce2", font=("Segoe UI", 11))
            return

        scale = min(width, height * 1.42) * 0.50 * self.zoom
        cosine, sine = math.cos(self.yaw), math.sin(self.yaw)
        projected = {}
        for number, (x, y, z, color) in self.points.items():
            x, y, z = x - self.center[0], y - self.center[1], z - self.center[2]
            depth = z * cosine - x * sine
            perspective = 1 / (1 - 0.28 * depth)
            projected[number] = (width / 2 + (x * cosine + z * sine) * scale * perspective,
                                 height / 2 + y * scale * perspective, depth, color)
        for numbers in self.groups:
            for a, b in zip(numbers, numbers[1:]):
                canvas.create_line(*projected[a][:2], *projected[b][:2], fill="#344563", width=1)
        for number, (x, y, depth, color) in sorted(projected.items(), key=lambda item: item[1][2]):
            tags = (f"slide-{number}",)
            marked = number in self.sources or number == self.selected
            size = (13 if marked else 9) + depth * 2
            if marked:
                canvas.create_oval(x-size-6, y-size-6, x+size+6, y+size+6, outline=color, width=2, tags=tags)
            canvas.create_oval(x-size, y-size, x+size, y+size, fill=color, outline="", tags=tags)
            canvas.create_text(x, y, text=str(number), fill=BACKGROUND, font=("Segoe UI", 8, "bold"), tags=tags)
