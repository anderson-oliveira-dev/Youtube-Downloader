import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, ttk
import threading
import queue
import yt_dlp
import os
import json
import sys
import subprocess
from pathlib import Path


CONFIG_PATH = Path.home() / ".youtube_downloader_config.json"


def get_default_directory():
    videos = Path.home() / "Videos"
    if videos.exists() and videos.is_dir():
        return str(videos)
    return str(Path.home())


def load_last_directory():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        path = data.get("last_directory")
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return get_default_directory()


def save_last_directory(directory):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_directory": directory}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def format_speed(speed):
    if not speed:
        return "N/A"
    try:
        value = float(speed)
    except (TypeError, ValueError):
        return "N/A"
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    i = 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    return f"{value:.2f} {units[i]}"


def format_eta(seconds):
    if seconds is None:
        return "N/A"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "N/A"
    if seconds < 0:
        return "N/A"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def resolve_output_path(info):
    candidates = []
    requested = info.get("requested_downloads") or []
    for item in requested:
        fp = item.get("filepath")
        if fp:
            candidates.append(fp)
    for key in ("filepath", "_filename"):
        fp = info.get(key)
        if fp:
            candidates.append(fp)

    for fp in candidates:
        if fp and os.path.exists(fp):
            return fp

    for fp in candidates:
        if not fp:
            continue
        base, _ = os.path.splitext(fp)
        for ext in (".mp3", ".mp4", ".mkv", ".webm", ".m4a", ".opus", ".ogg", ".aac", ".flac", ".wav"):
            alt = base + ext
            if os.path.exists(alt):
                return alt

    return candidates[0] if candidates else None


def reveal_in_explorer(filepath):
    if not filepath:
        return
    if sys.platform == "win32":
        if os.path.isfile(filepath):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(filepath)])
        else:
            folder = filepath if os.path.isdir(filepath) else os.path.dirname(filepath)
            if folder and os.path.isdir(folder):
                os.startfile(folder)
    elif sys.platform == "darwin":
        if os.path.isfile(filepath):
            subprocess.Popen(["open", "-R", filepath])
        else:
            folder = filepath if os.path.isdir(filepath) else os.path.dirname(filepath)
            subprocess.Popen(["open", folder or "."])
    else:
        folder = filepath if os.path.isdir(filepath) else os.path.dirname(filepath)
        if folder and os.path.isdir(folder):
            subprocess.Popen(["xdg-open", folder])


def open_with_default_app(filepath):
    if not filepath or not os.path.exists(filepath):
        raise FileNotFoundError(filepath)
    if sys.platform == "win32":
        os.startfile(filepath)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", filepath])
    else:
        subprocess.Popen(["xdg-open", filepath])


class ToolTip:
    def __init__(self, widget, text, delay=400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip_window = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _schedule(self, event=None):
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except Exception:
            self._after_id = None

    def _cancel(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self.tip_window:
            return
        try:
            if not self.widget.winfo_exists():
                return
        except Exception:
            return
        try:
            tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            label = tk.Label(
                tw, text=self.text,
                background="#2B2B2B", foreground="#E5E7EB",
                relief="solid", borderwidth=1,
                font=("Segoe UI", 9), padx=6, pady=3,
            )
            label.pack()
            tip_w = tw.winfo_reqwidth()
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - tip_w // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            tw.wm_geometry(f"+{x}+{y}")
            self.tip_window = tw
        except Exception:
            if self.tip_window:
                try:
                    self.tip_window.destroy()
                except Exception:
                    pass
            self.tip_window = None

    def _hide(self, event=None):
        self._cancel()
        if self.tip_window:
            try:
                self.tip_window.destroy()
            except Exception:
                pass
            self.tip_window = None

    def _on_destroy(self, event=None):
        self._hide()


class DownloadCancelled(Exception):
    pass


class YouTubeDownloaderApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("YouTube Downloader Pro")
        self.geometry("900x700")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.progress_queue = queue.Queue()
        self.downloading = False
        self.cancel_event = threading.Event()
        self._partial_files = set()
        self.history_files = {}
        self.action_buttons = {}
        self.tooltips = []
        self._refresh_scheduled = None

        self.create_widgets()
        self.check_queue()

    def create_widgets(self):
        config_frame = ctk.CTkFrame(self)
        config_frame.pack(pady=20, padx=20, fill="x")

        self.dir_label = ctk.CTkLabel(config_frame, text="Pasta de Destino:")
        self.dir_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.dir_entry = ctk.CTkEntry(config_frame, width=400, placeholder_text="Selecione a pasta...")
        self.dir_entry.grid(row=0, column=1, padx=10, pady=10)
        self.dir_entry.insert(0, load_last_directory())
        self.dir_button = ctk.CTkButton(config_frame, text="Procurar", width=100, command=self.select_directory)
        self.dir_button.grid(row=0, column=2, padx=10, pady=10)

        self.url_label = ctk.CTkLabel(config_frame, text="URL do Vídeo:")
        self.url_label.grid(row=1, column=0, padx=10, pady=10, sticky="w")
        self.url_entry = ctk.CTkEntry(config_frame, width=400, placeholder_text="Cole a URL do YouTube aqui")
        self.url_entry.grid(row=1, column=1, padx=10, pady=10, columnspan=2, sticky="w")

        self.quality_label = ctk.CTkLabel(config_frame, text="Qualidade:")
        self.quality_label.grid(row=2, column=0, padx=10, pady=10, sticky="w")
        self.quality_combobox = ctk.CTkComboBox(config_frame, values=["Melhor", "1080p", "720p", "480p", "360p"])
        self.quality_combobox.grid(row=2, column=1, padx=10, pady=10, sticky="w")
        self.quality_combobox.set("Melhor")

        self.type_var = tk.StringVar(value="video")
        self.type_switch = ctk.CTkSegmentedButton(config_frame, values=["Vídeo", "Áudio"], variable=self.type_var)
        self.type_switch.grid(row=2, column=2, padx=10, pady=10, sticky="e")

        self.download_button = ctk.CTkButton(
            self, text="INICIAR DOWNLOAD", height=50,
            font=("Arial", 16, "bold"),
            command=self.toggle_download
        )
        self.download_button.pack(pady=10, padx=20, fill="x")

        self.default_button_color = self.download_button.cget("fg_color")
        self.default_button_hover = self.download_button.cget("hover_color")

        progress_frame = ctk.CTkFrame(self)
        progress_frame.pack(pady=10, padx=20, fill="x")

        self.progress_bar = ctk.CTkProgressBar(progress_frame)
        self.progress_bar.pack(pady=(10, 5), padx=10, fill="x")
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(progress_frame, text="Aguardando...", anchor="w")
        self.status_label.pack(pady=(0, 10), padx=10, fill="x")

        history_frame = ctk.CTkFrame(self)
        history_frame.pack(pady=10, padx=20, fill="both", expand=True)

        try:
            style = ttk.Style()
            style.configure("Treeview", rowheight=32)
        except Exception:
            pass

        columns = ("Título", "Tipo", "Qualidade", "Status", "Ações")
        self.history_table = ttk.Treeview(history_frame, columns=columns, show="headings", height=8)
        for col in columns:
            self.history_table.heading(col, text=col)
            if col == "Ações":
                self.history_table.column(col, width=110, minwidth=90, anchor="center", stretch=False)
            else:
                self.history_table.column(col, width=150, anchor="w")
        self.history_table.pack(fill="both", expand=True, padx=10, pady=10)

        self.history_table.bind("<Configure>", self._schedule_refresh)
        self.history_table.bind("<MouseWheel>", self._schedule_refresh)
        self.history_table.bind("<Button-4>", self._schedule_refresh)
        self.history_table.bind("<Button-5>", self._schedule_refresh)
        self.history_table.bind("<Double-1>", self._on_history_double_click)

    def set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.dir_entry.configure(state=state)
        self.dir_button.configure(state=state)
        self.url_entry.configure(state=state)
        self.quality_combobox.configure(state=state)
        self.type_switch.configure(state=state)

    def set_button_stop_mode(self):
        self.download_button.configure(
            text="PARAR DOWNLOAD",
            fg_color="#DC3545",
            hover_color="#A71D2A",
        )

    def set_button_start_mode(self):
        self.download_button.configure(
            text="INICIAR DOWNLOAD",
            fg_color=self.default_button_color,
            hover_color=self.default_button_hover,
        )

    def reset_download_state(self):
        self.downloading = False
        self.set_controls_enabled(True)
        self.set_button_start_mode()

    def select_directory(self):
        initial = self.dir_entry.get().strip() or load_last_directory()
        directory = filedialog.askdirectory(initialdir=initial)
        if directory:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, directory)
            save_last_directory(directory)

    def toggle_download(self):
        if self.downloading:
            self.cancel_event.set()
            self.update_status("Cancelando download...")
            self.download_button.configure(state="disabled")
        else:
            self.start_download_thread()

    def start_download_thread(self):
        url = self.url_entry.get().strip()
        directory = self.dir_entry.get().strip()
        quality = self.quality_combobox.get()
        media_type = self.type_var.get()

        if not url or not directory:
            self.update_status("Erro: URL e pasta de destino são obrigatórios.", is_error=True)
            return

        if not os.path.isdir(directory):
            self.update_status("Erro: pasta de destino não existe.", is_error=True)
            return

        save_last_directory(directory)

        self.cancel_event.clear()
        self._partial_files.clear()

        self.downloading = True
        self.set_controls_enabled(False)
        self.set_button_stop_mode()

        self.progress_bar.set(0)
        self.update_status("Iniciando download...")

        download_thread = threading.Thread(
            target=self.download_video,
            args=(url, directory, quality, media_type),
            daemon=True,
        )
        download_thread.start()

    def download_video(self, url, directory, quality, media_type):
        ydl_opts = {
            'outtmpl': os.path.join(directory, '%(title)s.%(ext)s'),
            'progress_hooks': [self.progress_hook],
            'quiet': True,
            'no_warnings': True,
        }

        if media_type == "Áudio":
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            height_map = {"Melhor": None, "1080p": 1080, "720p": 720, "480p": 480, "360p": 360}
            target_height = height_map.get(quality)

            if target_height:
                ydl_opts['format'] = f'bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]'
            else:
                ydl_opts['format'] = 'bestvideo+bestaudio/best'

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'Desconhecido')
                filepath = resolve_output_path(info)
                self.progress_queue.put(('finished', title, media_type, filepath))
        except DownloadCancelled:
            self._cleanup_partial_files()
            self.progress_queue.put(('cancelled', None, None))
        except Exception as e:
            self._cleanup_partial_files()
            self.progress_queue.put(('error', str(e), None))

    def _cleanup_partial_files(self):
        for filepath in list(self._partial_files):
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
        self._partial_files.clear()

    def progress_hook(self, d):
        if self.cancel_event.is_set():
            raise DownloadCancelled()

        if d['status'] == 'downloading':
            filename = d.get('filename')
            tmpfilename = d.get('tmpfilename')
            if filename:
                self._partial_files.add(filename)
            if tmpfilename:
                self._partial_files.add(tmpfilename)

            downloaded = d.get('downloaded_bytes') or 0
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0

            if total > 0:
                percent_float = downloaded / total
            else:
                percent_float = 0.0
            percent_float = max(0.0, min(1.0, percent_float))

            percent_display = f"{percent_float * 100:.1f}%"
            speed_display = format_speed(d.get('speed'))
            eta_display = format_eta(d.get('eta'))

            self.progress_queue.put((
                'progress',
                percent_float,
                f"Baixando: {percent_display} | Velocidade: {speed_display} | Restante: {eta_display}",
            ))

        elif d['status'] == 'finished':
            filename = d.get('filename')
            if filename:
                self._partial_files.add(filename)
            self.progress_queue.put((
                'processing',
                1.0,
                "Download concluído. Processando com FFmpeg (mesclando/convertendo)...",
            ))

    def check_queue(self):
        try:
            while True:
                message = self.progress_queue.get_nowait()
                msg_type = message[0]

                if msg_type == 'progress':
                    _, percent, text = message
                    self.progress_bar.set(percent)
                    self.update_status(text)

                elif msg_type == 'processing':
                    _, percent, text = message
                    if percent is not None:
                        self.progress_bar.set(percent)
                    self.update_status(text)

                elif msg_type == 'finished':
                    _, title, media_type, filepath = message
                    self.progress_bar.set(1)
                    self.update_status(f"Download de '{title}' concluído com sucesso!")
                    self.add_to_history(title, media_type, "Concluído", filepath)
                    self._partial_files.clear()
                    self.download_button.configure(state="normal")
                    self.reset_download_state()

                elif msg_type == 'cancelled':
                    self.progress_bar.set(0)
                    self.update_status("Download cancelado pelo usuário.")
                    self.add_to_history("(cancelado)", self._friendly_type(), "Cancelado")
                    self.download_button.configure(state="normal")
                    self.reset_download_state()

                elif msg_type == 'error':
                    _, error_msg, _ = message
                    self.update_status(f"Erro: {error_msg}", is_error=True)
                    self.download_button.configure(state="normal")
                    self.reset_download_state()

        except queue.Empty:
            pass
        finally:
            self.after(100, self.check_queue)

    def _friendly_type(self):
        return "Áudio" if self.type_var.get() == "Áudio" else "Vídeo"

    def update_status(self, text, is_error=False):
        color = "#FF5555" if is_error else "#FFFFFF"
        self.status_label.configure(text=text, text_color=color)

    def add_to_history(self, title, media_type, status, filepath=None):
        display_type = media_type if media_type in ("Áudio", "Vídeo") else self._friendly_type()
        item_id = self.history_table.insert(
            "", 0,
            values=(title, display_type, self.quality_combobox.get(), status, "")
        )
        if filepath:
            self.history_files[item_id] = filepath
        self.after_idle(self._refresh_action_buttons)

    def _schedule_refresh(self, event=None):
        if self._refresh_scheduled:
            try:
                self.after_cancel(self._refresh_scheduled)
            except Exception:
                pass
        self._refresh_scheduled = self.after(30, self._do_refresh)

    def _do_refresh(self):
        self._refresh_scheduled = None
        self._refresh_action_buttons()

    def _refresh_action_buttons(self):
        for btns in self.action_buttons.values():
            for b in btns:
                try:
                    b.destroy()
                except Exception:
                    pass
        self.action_buttons.clear()
        self.tooltips.clear()

        try:
            items = self.history_table.get_children()
        except Exception:
            return

        for item in items:
            filepath = self.history_files.get(item)
            if not filepath:
                continue
            try:
                bbox = self.history_table.bbox(item, "Ações")
            except Exception:
                bbox = None
            if not bbox or len(bbox) < 4:
                continue
            x, y, w, h = bbox
            if w <= 0 or h <= 0:
                continue

            half = w // 2
            btn_h = max(min(h - 4, 28), 20)

            folder_btn = ctk.CTkButton(
                self.history_table,
                text="📂", width=half - 4, height=btn_h,
                font=("Segoe UI Emoji", 12),
                corner_radius=4,
                fg_color="#3B82F6", hover_color="#2563EB",
                text_color="#FFFFFF",
                command=lambda p=filepath: self._open_folder(p),
            )
            folder_btn.place(x=x + 2, y=y + (h - btn_h) // 2, anchor="nw")

            play_btn = ctk.CTkButton(
                self.history_table,
                text="▶", width=half - 4, height=btn_h,
                font=("Segoe UI Emoji", 12),
                corner_radius=4,
                fg_color="#10B981", hover_color="#059669",
                text_color="#FFFFFF",
                command=lambda p=filepath: self._open_file(p),
            )
            play_btn.place(x=x + half + 1, y=y + (h - btn_h) // 2, anchor="nw")

            self.action_buttons[item] = (folder_btn, play_btn)
            self.tooltips.append(ToolTip(folder_btn, "Abrir pasta do arquivo"))
            self.tooltips.append(ToolTip(play_btn, "Abrir o arquivo"))

    def _open_folder(self, filepath):
        try:
            reveal_in_explorer(filepath)
        except Exception as e:
            self.update_status(f"Erro ao abrir local: {e}", is_error=True)

    def _open_file(self, filepath):
        if not os.path.isfile(filepath):
            self.update_status("Arquivo não encontrado.", is_error=True)
            return
        try:
            open_with_default_app(filepath)
        except Exception as e:
            self.update_status(f"Erro ao abrir arquivo: {e}", is_error=True)

    def _on_history_double_click(self, event):
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            widget = None
        while widget is not None:
            if isinstance(widget, ctk.CTkButton):
                return
            if widget is self.history_table:
                break
            widget = getattr(widget, "master", None)

        row_id = self.history_table.identify_row(event.y)
        if not row_id:
            return
        filepath = self.history_files.get(row_id)
        if not filepath or not os.path.isfile(filepath):
            return
        try:
            open_with_default_app(filepath)
        except Exception as e:
            self.update_status(f"Erro ao abrir arquivo: {e}", is_error=True)


if __name__ == "__main__":
    app = YouTubeDownloaderApp()
    app.mainloop()