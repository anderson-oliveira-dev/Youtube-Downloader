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
import re
from pathlib import Path


CONFIG_PATH = Path.home() / ".youtube_downloader_config.json"

EXTRACTOR_ARGS = {
    'youtube': {
        'player_client': ['web_safari', 'android', 'tv', 'mweb']
    },
    'youtubepot-bgutilscript': {
        'script_path': r'C:\Users\Usuário 1\bgutil-ytdlp-pot-provider\server\src\generate_once.ts'
    }
}

PLAYLIST_DEFAULT_HEIGHTS = [1080, 720, 480, 360]


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


def pick_best_option(options):
    if not options:
        return None
    video_opts = [o for o in options if re.match(r"(MP4|AVI)\s*-\s*\d+p", o)]
    if video_opts:
        def res(o):
            m = re.search(r"(\d+)p", o)
            return int(m.group(1)) if m else 0
        mp4_opts = [o for o in video_opts if o.startswith("MP4")]
        if mp4_opts:
            return max(mp4_opts, key=res)
        return max(video_opts, key=res)
    audio_opts = [o for o in options if "kbps" in o]
    if audio_opts:
        def kbps(o):
            m = re.search(r"(\d+)\s*kbps", o)
            return int(m.group(1)) if m else 0
        return max(audio_opts, key=kbps)
    return options[0]


def resolve_output_path(info):
    if not info:
        return None
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
        for ext in (".mp3", ".mp4", ".mkv", ".webm", ".m4a", ".opus", ".ogg", ".aac", ".flac", ".wav", ".avi"):
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


def is_youtube_music_url(url):
    return bool(re.search(r"music\.youtube\.com", url or "", re.IGNORECASE))


def is_youtube_music_info(info):
    ext_key = (info.get("extractor_key") or "").lower()
    return "youtubemusic" in ext_key


def is_playlist_info(info):
    if not info:
        return False
    return info.get("_type") == "playlist" or "entries" in info


def collect_video_heights(formats):
    heights = set()
    for f in formats:
        if f.get("vcodec") == "none":
            continue
        h = f.get("height")
        if isinstance(h, (int, float)) and h > 0:
            heights.add(int(h))
    return sorted(heights, reverse=True)


def build_video_quality_options(heights):
    options = []
    for h in heights:
        options.append(f"MP4 - {h}p")
        options.append(f"AVI - {h}p")
    return options


def build_audio_quality_options():
    return ["MP3 - 320 kbps", "MP3 - 192 kbps", "MP3 - 128 kbps"]


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
        self.geometry("900x760")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.progress_queue = queue.Queue()
        self.analysis_queue = queue.Queue()
        self.downloading = False
        self.analyzing = False
        self.cancel_event = threading.Event()
        self._partial_files = set()
        self.history_files = {}
        self.action_buttons = {}
        self.tooltips = []
        self._refresh_scheduled = None
        self.video_formats = None
        self.audio_formats = None
        self.current_is_playlist = False

        self.create_widgets()
        self.check_queue()
        self.check_analysis_queue()

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
        self.url_entry.grid(row=1, column=1, padx=10, pady=10, sticky="w")
        self.url_entry.bind("<Control-v>", self._on_url_paste)
        self.url_entry.bind("<Control-V>", self._on_url_paste)

        self.analyze_button = ctk.CTkButton(
            config_frame, text="Analisar Link", width=110,
            command=self.start_analysis_thread
        )
        self.analyze_button.grid(row=1, column=2, padx=10, pady=10)

        self.quality_label = ctk.CTkLabel(config_frame, text="Qualidade:")
        self.quality_label.grid(row=2, column=0, padx=10, pady=10, sticky="w")
        self.quality_combobox = ctk.CTkComboBox(config_frame, values=["Aguardando análise..."])
        self.quality_combobox.grid(row=2, column=1, padx=10, pady=10, sticky="w")
        self.quality_combobox.set("Aguardando análise...")

        self.type_var = tk.StringVar(value="Vídeo")
        self.type_switch = ctk.CTkSegmentedButton(
            config_frame, values=["Vídeo", "Áudio"],
            variable=self.type_var, command=self._on_type_changed,
            state="disabled"
        )
        self.type_switch.grid(row=2, column=2, padx=10, pady=10, sticky="e")

        self.download_button = ctk.CTkButton(
            self, text="INICIAR DOWNLOAD", height=50,
            font=("Arial", 16, "bold"),
            command=self.toggle_download,
            state="disabled"
        )
        self.download_button.pack(pady=10, padx=20, fill="x")

        self.default_button_color = self.download_button.cget("fg_color")
        self.default_button_hover = self.download_button.cget("hover_color")

        progress_frame = ctk.CTkFrame(self)
        progress_frame.pack(pady=10, padx=20, fill="x")

        self.progress_bar = ctk.CTkProgressBar(progress_frame)
        self.progress_bar.pack(pady=(10, 5), padx=10, fill="x")
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(progress_frame, text="Cole um link e clique em 'Analisar Link'.", anchor="w")
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
        self.analyze_button.configure(state=state)
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

    def _on_url_paste(self, event=None):
        self.after(50, self.start_analysis_thread)

    def start_analysis_thread(self):
        if self.analyzing:
            return
        url = self.url_entry.get().strip()
        if not url:
            self.update_status("Erro: cole uma URL válida primeiro.", is_error=True)
            return

        self.analyzing = True
        self.analyze_button.configure(state="disabled", text="Analisando...")
        self.type_switch.configure(state="disabled")
        self.quality_combobox.configure(state="disabled", values=["Analisando..."])
        self.quality_combobox.set("Analisando...")
        self.download_button.configure(state="disabled")
        self.update_status("Buscando informações do link...")

        thread = threading.Thread(target=self._analyze_url, args=(url,), daemon=True)
        thread.start()

    def _analyze_url(self, url):
        try:
            flat_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'extract_flat': 'in_playlist',
                'extractor_args': EXTRACTOR_ARGS,
                'force_ipv4': True,
            }
            with yt_dlp.YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if info is None:
                self.analysis_queue.put({"ok": False, "error": "Não foi possível obter informações do link."})
                return

            is_music = is_youtube_music_url(url) or is_youtube_music_info(info)
            playlist = is_playlist_info(info)

            if playlist:
                entries = list(info.get("entries") or [])
                entry_count = len(entries)
                playlist_title = info.get("title") or "Playlist"

                heights = []
                if entries:
                    first_url = entries[0].get("url") or entries[0].get("webpage_url")
                    if first_url:
                        try:
                            single_opts = {
                                'quiet': True,
                                'no_warnings': True,
                                'skip_download': True,
                                'extractor_args': EXTRACTOR_ARGS,
                                'force_ipv4': True,
                            }
                            with yt_dlp.YoutubeDL(single_opts) as ydl2:
                                first_info = ydl2.extract_info(first_url, download=False)
                            if first_info:
                                heights = collect_video_heights(first_info.get("formats") or [])
                        except Exception:
                            heights = []

                if not heights:
                    heights = list(PLAYLIST_DEFAULT_HEIGHTS)

                result = {
                    "ok": True,
                    "is_playlist": True,
                    "is_music": is_music,
                    "heights": heights,
                    "video_options": build_video_quality_options(heights),
                    "audio_options": build_audio_quality_options(),
                    "title": f"{playlist_title} ({entry_count} itens)",
                }
                self.analysis_queue.put(result)
                return

            result = {
                "ok": True,
                "is_playlist": False,
                "is_music": is_music,
                "heights": [],
                "video_options": build_video_quality_options(
                    collect_video_heights(info.get("formats") or [])
                ),
                "audio_options": build_audio_quality_options(),
                "title": info.get("title") or "Título desconhecido",
            }
            self.analysis_queue.put(result)
        except Exception as e:
            self.analysis_queue.put({"ok": False, "error": str(e)})

    def check_analysis_queue(self):
        try:
            while True:
                result = self.analysis_queue.get_nowait()
                self._apply_analysis_result(result)
        except queue.Empty:
            pass
        finally:
            self.after(100, self.check_analysis_queue)

    def _apply_analysis_result(self, result):
        self.analyzing = False
        self.analyze_button.configure(state="normal", text="Analisar Link")

        if not result.get("ok"):
            self.update_status(f"Falha ao analisar link: {result.get('error')}", is_error=True)
            self.type_switch.configure(state="disabled")
            self.quality_combobox.configure(state="disabled", values=["Falha na análise"])
            self.quality_combobox.set("Falha na análise")
            self.download_button.configure(state="disabled")
            return

        self.video_formats = result.get("video_options") or []
        self.audio_formats = result.get("audio_options") or []
        self.current_is_playlist = result.get("is_playlist", False)

        if result.get("is_music"):
            self.type_var.set("Áudio")
            prefix = "Playlist de música" if self.current_is_playlist else "YouTube Music"
            self.update_status(f"{prefix} detectado: {result.get('title')}. Modo Áudio ativado.")
        else:
            self.type_var.set("Vídeo")
            prefix = "Playlist" if self.current_is_playlist else "Link"
            self.update_status(f"{prefix} analisado: {result.get('title')}")

        self.type_switch.configure(state="normal")
        self._on_type_changed()

    def _on_type_changed(self, *args):
        media_type = self.type_var.get()
        if media_type == "Áudio":
            options = self.audio_formats or []
        else:
            options = self.video_formats or []

        if not options:
            options = ["Nenhuma opção disponível"]

        best = pick_best_option(options) or options[0]

        self.quality_combobox.configure(state="normal")
        self.quality_combobox.configure(values=options)
        self.quality_combobox.set(best)
        self.download_button.configure(state="normal")

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

        if quality in ("Aguardando análise...", "Analisando...", "Falha na análise", "Nenhuma opção disponível"):
            self.update_status("Erro: analise o link antes de baixar.", is_error=True)
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

    def _make_progress_hook(self, idx=None, total=None):
        def hook(d):
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
                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0

                if total_bytes > 0:
                    percent_float = downloaded / total_bytes
                else:
                    percent_float = 0.0
                percent_float = max(0.0, min(1.0, percent_float))

                percent_display = f"{percent_float * 100:.1f}%"
                speed_display = format_speed(d.get('speed'))
                eta_display = format_eta(d.get('eta'))

                item_title = (d.get('info_dict') or {}).get('title') or ''
                if len(item_title) > 35:
                    item_title = item_title[:32] + "..."

                if idx is not None and total is not None:
                    prefix = f"[{idx}/{total}] "
                else:
                    prefix = ""
                if item_title:
                    prefix += f"{item_title}: "

                self.progress_queue.put((
                    'progress',
                    percent_float,
                    f"{prefix}Baixando: {percent_display} | Velocidade: {speed_display} | Restante: {eta_display}",
                ))

            elif d['status'] == 'finished':
                filename = d.get('filename')
                if filename:
                    self._partial_files.add(filename)
                self.progress_queue.put((
                    'processing',
                    1.0,
                    "Processando com FFmpeg (mesclando/convertendo)...",
                ))
        return hook

    def _build_ydl_opts(self, directory, quality, media_type):
        opts = {
            'outtmpl': os.path.join(directory, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'extractor_args': EXTRACTOR_ARGS,
            'force_ipv4': True,
            'sleep_interval': 2,
        }

        if media_type == "Áudio":
            bitrate = 192
            m = re.search(r"(\d+)\s*kbps", quality or "")
            if m:
                bitrate = int(m.group(1))
            opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': str(bitrate),
                }],
            })
        else:
            m = re.match(r"(MP4|AVI)\s*-\s*(\d+)p", quality or "")
            if m:
                container = m.group(1).lower()
                height = int(m.group(2))
                opts['format'] = f'bestvideo[height<={height}]+bestaudio/best[height<={height}]'
                opts['merge_output_format'] = container
                opts['postprocessors'] = [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': container,
                }]
            else:
                opts['format'] = 'bestvideo+bestaudio/best'
                opts['merge_output_format'] = 'mp4'

        return opts

    def download_video(self, url, directory, quality, media_type):
        try:
            flat_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'extract_flat': 'in_playlist',
                'extractor_args': EXTRACTOR_ARGS,
                'force_ipv4': True,
            }
            with yt_dlp.YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            self._cleanup_partial_files()
            self.progress_queue.put(('error', str(e), None))
            return

        if info is None:
            self.progress_queue.put(('error', 'Falha ao processar o link.', None))
            return

        base_opts = self._build_ydl_opts(directory, quality, media_type)

        if not is_playlist_info(info):
            self._partial_files.clear()
            opts = dict(base_opts)
            opts['progress_hooks'] = [self._make_progress_hook()]
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    result = ydl.extract_info(url, download=True)
                title = (result or {}).get('title', 'Desconhecido')
                filepath = resolve_output_path(result)
                self.progress_queue.put(('finished', title, media_type, filepath))
            except DownloadCancelled:
                self._cleanup_partial_files()
                self.progress_queue.put(('cancelled', None, None))
            except Exception as e:
                self._cleanup_partial_files()
                self.progress_queue.put(('error', str(e), None))
            return

        entries = [e for e in (info.get('entries') or []) if e]
        playlist_title = info.get('title') or 'Playlist'
        total = len(entries)
        success = 0
        was_cancelled = False

        self.progress_queue.put(('playlist_started', playlist_title, total))

        for idx, entry in enumerate(entries, 1):
            if self.cancel_event.is_set():
                was_cancelled = True
                break

            entry_url = entry.get('url') or entry.get('webpage_url')
            entry_title = entry.get('title') or f'Item {idx}'

            if not entry_url:
                self.progress_queue.put(('item_finished', entry_title, media_type, None, idx, total))
                continue

            self._partial_files.clear()
            self.progress_queue.put(('item_started', entry_title, idx, total))

            opts = dict(base_opts)
            opts['progress_hooks'] = [self._make_progress_hook(idx, total)]

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    result = ydl.extract_info(entry_url, download=True)

                filepath = resolve_output_path(result)
                if filepath:
                    success += 1

                if self.cancel_event.is_set():
                    self.progress_queue.put(('item_finished', entry_title, media_type, filepath, idx, total))
                    was_cancelled = True
                    break

                self.progress_queue.put(('item_finished', entry_title, media_type, filepath, idx, total))
            except DownloadCancelled:
                self._cleanup_partial_files()
                was_cancelled = True
                break
            except Exception:
                self._cleanup_partial_files()
                self.progress_queue.put(('item_finished', entry_title, media_type, None, idx, total))

        if was_cancelled or self.cancel_event.is_set():
            self.progress_queue.put(('cancelled', None, None))
        else:
            self.progress_queue.put(('playlist_finished', playlist_title, success, total))

    def _cleanup_partial_files(self):
        for filepath in list(self._partial_files):
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
        self._partial_files.clear()

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

                elif msg_type == 'playlist_started':
                    _, playlist_title, total = message
                    self.progress_bar.set(0)
                    self.update_status(f"Iniciando playlist '{playlist_title}' ({total} itens)...")

                elif msg_type == 'item_started':
                    _, entry_title, idx, total = message
                    self.update_status(f"[{idx}/{total}] Preparando: {entry_title}")

                elif msg_type == 'item_finished':
                    _, title, media_type, filepath, idx, total = message
                    status = "Concluído" if filepath else "Falhou"
                    self.add_to_history(title, media_type, status, filepath)
                    self.update_status(f"[{idx}/{total}] {status}: {title}")

                elif msg_type == 'playlist_finished':
                    _, playlist_title, success, total = message
                    self.progress_bar.set(1)
                    self.update_status(
                        f"Playlist '{playlist_title}' concluída: {success}/{total} itens baixados."
                    )
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