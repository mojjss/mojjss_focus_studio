from __future__ import annotations

from datetime import datetime

import customtkinter as ctk
import matplotlib.dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure


class AnalyticsChart(ctk.CTkFrame):
    WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def __init__(self, master):
        super().__init__(master, corner_radius=12)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.figure = Figure(figsize=(10, 6.5), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().grid(
            row=0, column=0, sticky="nsew", padx=8, pady=(8, 0)
        )

        toolbar_host = ctk.CTkFrame(self, fg_color="transparent", height=34)
        toolbar_host.grid(row=1, column=0, sticky="ew")
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_host, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="left", padx=8, pady=2)

    def draw(
        self,
        daily_focus,
        daily_other_productive,
        categories,
        weekdays_focus,
        weekdays_other_productive,
        appearance: str,
    ) -> None:
        self.figure.clear()
        is_dark = appearance.lower() == "dark"
        background = "#1f242b" if is_dark else "#ffffff"
        foreground = "#e8edf2" if is_dark else "#1f2937"
        muted = "#9ca3af" if is_dark else "#6b7280"
        grid = "#374151" if is_dark else "#e5e7eb"
        focus_color = "#3b82f6"
        productive_color = "#14b8a6"

        self.figure.set_facecolor(background)
        ax_daily = self.figure.add_subplot(221)
        ax_category = self.figure.add_subplot(222)
        ax_weekday = self.figure.add_subplot(212)

        axes = [ax_daily, ax_category, ax_weekday]
        for ax in axes:
            ax.set_facecolor(background)
            ax.tick_params(colors=muted, labelsize=9)
            for spine in ax.spines.values():
                spine.set_visible(False)
            ax.grid(True, axis="y", color=grid, linewidth=0.8, alpha=0.8)
            ax.title.set_color(foreground)

        focus_map = {date: value for date, value in daily_focus}
        other_map = {date: value for date, value in daily_other_productive}
        all_dates = sorted(set(focus_map) | set(other_map))
        if all_dates:
            dates = [datetime.strptime(item, "%Y%m%d") for item in all_dates]
            focus_values = [focus_map.get(item, 0) for item in all_dates]
            other_values = [other_map.get(item, 0) for item in all_dates]
            ax_daily.plot(
                dates,
                focus_values,
                marker="o",
                linewidth=2.2,
                color=focus_color,
                label="Focus",
            )
            ax_daily.plot(
                dates,
                other_values,
                marker="o",
                linewidth=1.8,
                color=productive_color,
                label="Other productive",
            )
            ax_daily.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
            ax_daily.tick_params(axis="x", rotation=30)
            ax_daily.set_title("Productive minutes by day", loc="left", fontsize=11)
            ax_daily.set_ylabel("minutes", color=muted)
            legend = ax_daily.legend(frameon=False, fontsize=8)
            for text in legend.get_texts():
                text.set_color(foreground)
        else:
            self._empty(ax_daily, "No productive sessions yet", foreground, muted)

        if categories:
            top = categories[:8]
            labels = [item[0] for item in reversed(top)]
            values = [item[1] for item in reversed(top)]
            ax_category.barh(labels, values, color=productive_color, alpha=0.85)
            ax_category.set_title("Total productive time by category", loc="left", fontsize=11)
            ax_category.set_xlabel("minutes", color=muted)
        else:
            self._empty(ax_category, "No productive categories yet", foreground, muted)

        focus_values = [dict(weekdays_focus).get(index, 0) for index in range(7)]
        other_values = [
            dict(weekdays_other_productive).get(index, 0) for index in range(7)
        ]
        positions = list(range(7))
        width = 0.38
        ax_weekday.bar(
            [value - width / 2 for value in positions],
            focus_values,
            width=width,
            color=focus_color,
            alpha=0.9,
            label="Focus",
        )
        ax_weekday.bar(
            [value + width / 2 for value in positions],
            other_values,
            width=width,
            color=productive_color,
            alpha=0.85,
            label="Other productive",
        )
        ax_weekday.set_xticks(positions, self.WEEKDAYS)
        ax_weekday.set_title("Work pattern by weekday", loc="left", fontsize=11)
        ax_weekday.set_ylabel("minutes", color=muted)
        legend = ax_weekday.legend(frameon=False, fontsize=8)
        for text in legend.get_texts():
            text.set_color(foreground)

        self.figure.tight_layout(pad=2.0)
        self.canvas.draw_idle()

    @staticmethod
    def _empty(ax, title: str, foreground: str, muted: str) -> None:
        ax.text(
            0.5,
            0.55,
            title,
            ha="center",
            va="center",
            transform=ax.transAxes,
            color=foreground,
        )
        ax.text(
            0.5,
            0.43,
            "Complete a session to populate this chart.",
            ha="center",
            va="center",
            transform=ax.transAxes,
            color=muted,
            fontsize=9,
        )
        ax.set_xticks([])
        ax.set_yticks([])
