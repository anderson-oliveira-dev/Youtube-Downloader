import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, ttk
import threading
import queue
import yt_dlp
import os
import re


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

        self.create_widgets()
        self.check_queue()

    def create_widgets(self):
        config_frame = ctk.CTkFrame(self)
        config_frame.pack(pady=20, padx=20, fill="x")

        self.dir_label = ctk.CTkLabel(config_frame, text="Pasta de Destino:")
        self.dir_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.dir_entry = ctk.CTkEntry(config_frame, width=400, placeholder_text="Selecione a pasta...")
        self.dir_entry.grid(row=0, column=1, padx=10, pady=10)
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

        columns = ("Título", "Tipo", "Qualidade", "Status")
        self.history_table = ttk.Treeview(history_frame, columns=columns, show="headings", height=8)
        for col in columns:
            self.history_table.heading(col, text=col)
            self.history_table.column(col, width=150, anchor="w")
        self.history_table.pack(fill="both", expand=True, padx=10, pady=10)

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
        directory = filedialog.askdirectory()
        if directory:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, directory)

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
                self.progress_queue.put(('finished', title, media_type))
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

            percent_str = d.get('_percent_str', '0%').strip()
            speed_str = d.get('_speed_str', 'N/A')
            eta_str = d.get('_eta_str', 'N/A')

            ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
            percent_str = ansi_escape.sub('', percent_str)

            try:
                percent_float = float(percent_str.replace('%', '')) / 100
            except ValueError:
                percent_float = 0

            self.progress_queue.put((
                'progress',
                percent_float,
                f"Baixando: {percent_str} | Velocidade: {speed_str} | Restante: {eta_str}",
            ))

        elif d['status'] == 'finished':
            filename = d.get('filename')
            if filename:
                self._partial_files.add(filename)
            self.progress_queue.put((
                'processing',
                None,
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
                    self.update_status(message[2])

                elif msg_type == 'finished':
                    _, title, media_type = message
                    self.progress_bar.set(1)
                    self.update_status(f"Download de '{title}' concluído com sucesso!")
                    self.add_to_history(title, media_type, "Concluído")
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

    def add_to_history(self, title, media_type, status):
        display_type = media_type if media_type in ("Áudio", "Vídeo") else self._friendly_type()
        self.history_table.insert(
            "", 0,
            values=(title, display_type, self.quality_combobox.get(), status)
        )


if __name__ == "__main__":
    app = YouTubeDownloaderApp()
    app.mainloop()