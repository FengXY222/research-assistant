"""猜数字游戏 - Tkinter 图形界面版。"""

import random
import tkinter as tk
from tkinter import messagebox, ttk


MAX_ATTEMPTS = 10
CODE_LENGTH = 4


class GuessNumberGame(tk.Tk):
    """猜数字游戏主窗口。"""

    COLORS = {
        "background": "#0B1220",
        "panel": "#111B2E",
        "card": "#17243B",
        "card_light": "#1D2D49",
        "border": "#2B3D5D",
        "primary": "#7567F8",
        "primary_hover": "#877BFF",
        "teal": "#2DD4BF",
        "orange": "#F5A524",
        "text": "#F8FAFC",
        "muted": "#9AAAC2",
        "danger": "#FB7185",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("猜数字 · Number Guess")
        self.geometry("900x620")
        self.minsize(820, 560)
        self.configure(bg=self.COLORS["background"])
        self.protocol("WM_DELETE_WINDOW", self.close_window)

        self.answer = ""
        self.history: list[tuple[int, str, int]] = []
        self.hint_used = False
        self.game_over = False

        self.guess_var = tk.StringVar()
        self.attempt_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.hint_var = tk.StringVar()
        self.progress_var = tk.DoubleVar()

        self.setup_styles()
        self.build_ui()
        self.start_game()

    def setup_styles(self) -> None:
        """配置 ttk 控件样式。"""
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Game.Treeview",
            background=self.COLORS["card"],
            fieldbackground=self.COLORS["card"],
            foreground=self.COLORS["text"],
            borderwidth=0,
            rowheight=42,
            font=("Microsoft YaHei UI", 11),
        )
        style.configure(
            "Game.Treeview.Heading",
            background=self.COLORS["card_light"],
            foreground=self.COLORS["muted"],
            borderwidth=0,
            relief="flat",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Game.Treeview",
            background=[("selected", self.COLORS["card_light"])],
            foreground=[("selected", self.COLORS["text"])],
        )
        style.configure(
            "Game.Horizontal.TProgressbar",
            troughcolor=self.COLORS["card_light"],
            background=self.COLORS["teal"],
            bordercolor=self.COLORS["card_light"],
            lightcolor=self.COLORS["teal"],
            darkcolor=self.COLORS["teal"],
            thickness=8,
        )
        style.configure(
            "Game.Vertical.TScrollbar",
            background=self.COLORS["card_light"],
            troughcolor=self.COLORS["card"],
            bordercolor=self.COLORS["card"],
            arrowcolor=self.COLORS["muted"],
        )

    def build_ui(self) -> None:
        """构建窗口布局。"""
        self.grid_columnconfigure(0, weight=5)
        self.grid_columnconfigure(1, weight=6)
        self.grid_rowconfigure(0, weight=1)

        left = tk.Frame(self, bg=self.COLORS["background"])
        left.grid(row=0, column=0, sticky="nsew", padx=(34, 18), pady=(30, 18))
        left.grid_columnconfigure(0, weight=1)

        right = tk.Frame(self, bg=self.COLORS["background"])
        right.grid(row=0, column=1, sticky="nsew", padx=(18, 34), pady=(30, 18))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        title = tk.Frame(left, bg=self.COLORS["background"])
        title.grid(row=0, column=0, sticky="ew", pady=(0, 24))
        tk.Label(
            title,
            text="猜数字",
            bg=self.COLORS["background"],
            fg=self.COLORS["text"],
            font=("Microsoft YaHei UI", 28, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title,
            text="Number Guess  ·  找出隐藏的四位密码",
            bg=self.COLORS["background"],
            fg=self.COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(5, 0))

        status_card = self.card(left, 1)
        status_card.grid_columnconfigure(0, weight=1)
        tk.Label(
            status_card,
            text="本局进度",
            bg=self.COLORS["card"],
            fg=self.COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).grid(row=0, column=0, sticky="w")
        self.status_label = tk.Label(
            status_card,
            textvariable=self.status_var,
            bg=self.COLORS["card"],
            fg=self.COLORS["teal"],
            font=("Microsoft YaHei UI", 15, "bold"),
        )
        self.status_label.grid(row=1, column=0, sticky="w", pady=(6, 14))
        self.attempt_label = tk.Label(
            status_card,
            textvariable=self.attempt_var,
            bg=self.COLORS["card"],
            fg=self.COLORS["text"],
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        self.attempt_label.grid(row=0, column=1, rowspan=2, sticky="e")
        self.progress = ttk.Progressbar(
            status_card,
            variable=self.progress_var,
            maximum=MAX_ATTEMPTS,
            style="Game.Horizontal.TProgressbar",
        )
        self.progress.grid(row=2, column=0, columnspan=2, sticky="ew")

        guess_card = self.card(left, 2)
        guess_card.grid_columnconfigure(0, weight=1)
        tk.Label(
            guess_card,
            text="输入你的猜测",
            bg=self.COLORS["card"],
            fg=self.COLORS["text"],
            font=("Microsoft YaHei UI", 13, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            guess_card,
            text="输入 4 位数字，数字可以重复",
            bg=self.COLORS["card"],
            fg=self.COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).grid(row=1, column=0, sticky="w", pady=(5, 14))

        entry_row = tk.Frame(guess_card, bg=self.COLORS["card"])
        entry_row.grid(row=2, column=0, sticky="ew")
        entry_row.grid_columnconfigure(0, weight=1)
        validate_command = (self.register(self.validate_entry), "%P")
        self.entry = tk.Entry(
            entry_row,
            textvariable=self.guess_var,
            validate="key",
            validatecommand=validate_command,
            bg=self.COLORS["card_light"],
            fg=self.COLORS["text"],
            insertbackground=self.COLORS["text"],
            relief="flat",
            bd=0,
            justify="center",
            font=("Consolas", 24, "bold"),
        )
        self.entry.grid(row=0, column=0, sticky="ew", ipady=8, padx=(0, 10))
        self.entry.bind("<Return>", lambda _event: self.submit_guess())
        self.submit_button = self.action_button(
            entry_row, "提交猜测", self.submit_guess, self.COLORS["primary"]
        )
        self.submit_button.grid(row=0, column=1, sticky="ns")

        actions = tk.Frame(guess_card, bg=self.COLORS["card"])
        actions.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.hint_button = self.outline_button(actions, "使用一次提示", self.use_hint)
        self.hint_button.pack(side="left")
        self.exit_button = self.outline_button(actions, "退出本局", self.exit_round)
        self.exit_button.pack(side="right")
        tk.Label(
            guess_card,
            textvariable=self.hint_var,
            bg=self.COLORS["card"],
            fg=self.COLORS["orange"],
            font=("Microsoft YaHei UI", 10),
            wraplength=390,
            justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(14, 0))

        tips = tk.Frame(left, bg=self.COLORS["background"])
        tips.grid(row=3, column=0, sticky="ew", pady=(20, 0))
        tk.Label(
            tips,
            text="玩法提示",
            bg=self.COLORS["background"],
            fg=self.COLORS["muted"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            tips,
            text="“对 X 个”表示数字和位置都完全正确的数量。\n首位可以是 0，答案中的四个数字不会重复。",
            bg=self.COLORS["background"],
            fg=self.COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
            justify="left",
        ).pack(anchor="w", pady=(7, 0))

        history_title = tk.Frame(right, bg=self.COLORS["background"])
        history_title.grid(row=0, column=0, sticky="ew")
        tk.Label(
            history_title,
            text="猜测记录",
            bg=self.COLORS["background"],
            fg=self.COLORS["text"],
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(side="left")
        self.record_count_label = tk.Label(
            history_title,
            text="实时更新",
            bg=self.COLORS["background"],
            fg=self.COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        )
        self.record_count_label.pack(side="right", pady=(6, 0))

        self.notice = tk.Label(
            right,
            text="",
            bg=self.COLORS["background"],
            fg=self.COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
            anchor="w",
        )
        self.notice.grid(row=1, column=0, sticky="ew", pady=(8, 12))

        history_frame = tk.Frame(right, bg=self.COLORS["card"], highlightthickness=1, highlightbackground=self.COLORS["border"])
        history_frame.grid(row=2, column=0, sticky="nsew")
        history_frame.grid_rowconfigure(0, weight=1)
        history_frame.grid_columnconfigure(0, weight=1)
        self.history_tree = ttk.Treeview(
            history_frame,
            columns=("attempt", "guess", "result"),
            show="headings",
            style="Game.Treeview",
        )
        self.history_tree.heading("attempt", text="次数")
        self.history_tree.heading("guess", text="你的猜测")
        self.history_tree.heading("result", text="反馈")
        self.history_tree.column("attempt", width=90, anchor="center", stretch=False)
        self.history_tree.column("guess", width=150, anchor="center", stretch=True)
        self.history_tree.column("result", width=160, anchor="center", stretch=True)
        self.history_tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scrollbar = ttk.Scrollbar(
            history_frame,
            orient="vertical",
            command=self.history_tree.yview,
            style="Game.Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 5), pady=8)
        self.history_tree.configure(yscrollcommand=scrollbar.set)

        footer = tk.Label(
            self,
            text="每局最多 10 次 · 输入 exit 可退出 · hint 只可使用一次",
            bg=self.COLORS["background"],
            fg="#64748B",
            font=("Microsoft YaHei UI", 9),
        )
        footer.grid(row=1, column=0, columnspan=2, sticky="w", padx=34, pady=(0, 14))

    def card(self, parent: tk.Widget, row: int) -> tk.Frame:
        """创建统一风格的卡片容器。"""
        frame = tk.Frame(
            parent,
            bg=self.COLORS["card"],
            highlightthickness=1,
            highlightbackground=self.COLORS["border"],
            padx=18,
            pady=16,
        )
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        return frame

    def action_button(self, parent: tk.Widget, text: str, command, color: str) -> tk.Button:
        """创建主操作按钮。"""
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            activebackground=self.COLORS["primary_hover"],
            activeforeground="white",
            fg="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=20,
            pady=11,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        return button

    def outline_button(self, parent: tk.Widget, text: str, command) -> tk.Button:
        """创建次要操作按钮。"""
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.COLORS["card"],
            activebackground=self.COLORS["card_light"],
            activeforeground=self.COLORS["text"],
            fg=self.COLORS["muted"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=self.COLORS["border"],
            cursor="hand2",
            padx=12,
            pady=7,
            font=("Microsoft YaHei UI", 9),
        )

    @staticmethod
    def validate_entry(value: str) -> bool:
        """限制输入框最多为 4 位 ASCII 数字。"""
        return len(value) <= CODE_LENGTH and all(character in "0123456789" for character in value)

    def start_game(self) -> None:
        """开始或重置一局游戏。"""
        self.answer = "".join(random.sample("0123456789", CODE_LENGTH))
        self.history.clear()
        self.hint_used = False
        self.game_over = False
        self.guess_var.set("")
        self.hint_var.set("提示：答案四位数字互不重复，首位可以是 0")
        self.status_var.set("进行中")
        self.status_label.configure(fg=self.COLORS["teal"])
        self.attempt_var.set("0 / 10 次")
        self.progress_var.set(0)
        self.notice.configure(text="准备好了吗？输入你的第一个猜测。", fg=self.COLORS["muted"])
        self.record_count_label.configure(text="暂无记录")
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        self.set_controls(True)
        self.after(100, self.entry.focus_set)

    def set_controls(self, enabled: bool) -> None:
        """启用或禁用游戏控件。"""
        state = tk.NORMAL if enabled else tk.DISABLED
        self.entry.configure(state=state)
        self.submit_button.configure(state=state)
        self.hint_button.configure(state=state)
        self.exit_button.configure(state=state)

    def submit_guess(self) -> None:
        """处理玩家提交的猜测。"""
        if self.game_over:
            return
        guess = self.guess_var.get().strip()
        if guess.lower() == "hint":
            self.use_hint()
            return
        if guess.lower() == "exit":
            self.exit_round()
            return
        if len(guess) != CODE_LENGTH:
            self.show_error("请输入恰好 4 位数字，例如 0123。")
            return

        correct = sum(
            answer_digit == guess_digit
            for answer_digit, guess_digit in zip(self.answer, guess)
        )
        attempt = len(self.history) + 1
        self.history.append((attempt, guess, correct))
        self.history_tree.insert("", "end", values=(f"第 {attempt} 次", guess, f"对 {correct} 个"))
        self.history_tree.see(self.history_tree.get_children()[-1])
        self.guess_var.set("")
        self.progress_var.set(attempt)
        self.attempt_var.set(f"{attempt} / {MAX_ATTEMPTS} 次")
        self.record_count_label.configure(text=f"已记录 {attempt} 次")

        if guess == self.answer:
            self.finish_round(True)
        elif attempt >= MAX_ATTEMPTS:
            self.finish_round(False)
        else:
            remaining = MAX_ATTEMPTS - attempt
            self.notice.configure(text=f"→ 对 {correct} 个，还剩 {remaining} 次机会。", fg=self.COLORS["muted"])
            self.entry.focus_set()

    def use_hint(self) -> None:
        """使用一次提示，提示一个尚未确认的位置。"""
        if self.game_over:
            return
        if self.hint_used:
            self.show_error("提示只能使用一次。")
            return
        confirmed = {index for _, guess, _ in self.history for index in range(CODE_LENGTH) if guess[index] == self.answer[index]}
        available = [index for index in range(CODE_LENGTH) if index not in confirmed]
        index = random.choice(available or list(range(CODE_LENGTH)))
        self.hint_used = True
        self.hint_button.configure(text="提示已使用", state=tk.DISABLED)
        self.hint_var.set(f"提示：第 {index + 1} 位是 {self.answer[index]}（不消耗次数）")
        self.notice.configure(text="提示已揭晓，继续输入你的猜测。", fg=self.COLORS["orange"])
        self.entry.focus_set()

    def finish_round(self, won: bool) -> None:
        """结束本局并询问是否重新开始。"""
        self.game_over = True
        self.set_controls(False)
        if won:
            self.status_var.set("猜中了！")
            self.status_label.configure(fg=self.COLORS["teal"])
            self.notice.configure(text=f"答案是 {self.answer}，恭喜你完成挑战！", fg=self.COLORS["teal"])
            title = "恭喜猜中"
            message = f"你猜中了！\n正确答案是：{self.answer}\n\n要再来一局吗？"
        else:
            self.status_var.set("机会用完")
            self.status_label.configure(fg=self.COLORS["danger"])
            self.notice.configure(text=f"本局结束，正确答案是 {self.answer}。", fg=self.COLORS["danger"])
            title = "本局结束"
            message = f"10 次机会已用完。\n正确答案是：{self.answer}\n\n要再来一局吗？"
        self.after(180, lambda: self.ask_restart(title, message))

    def ask_restart(self, title: str, message: str) -> None:
        """弹出重新开始询问。"""
        if messagebox.askyesno(title, message, parent=self):
            self.start_game()

    def exit_round(self) -> None:
        """退出当前局。"""
        if self.game_over:
            return
        self.game_over = True
        self.set_controls(False)
        self.status_var.set("已退出")
        self.status_label.configure(fg=self.COLORS["orange"])
        self.notice.configure(text=f"本局已退出，正确答案是 {self.answer}。", fg=self.COLORS["orange"])
        self.after(100, lambda: self.ask_restart("退出本局", f"本局已退出。\n正确答案是：{self.answer}\n\n要重新开始吗？"))

    def show_error(self, message: str) -> None:
        """显示输入错误。"""
        self.notice.configure(text=message, fg=self.COLORS["danger"])
        self.entry.focus_set()

    def close_window(self) -> None:
        """关闭窗口。"""
        if self.game_over or messagebox.askyesno("退出游戏", "确定要退出当前游戏吗？", parent=self):
            self.destroy()


def main() -> None:
    app = GuessNumberGame()
    app.mainloop()


if __name__ == "__main__":
    main()
