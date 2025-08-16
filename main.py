# === HỆ THỐNG - CHUNG ===
import os
import re
import sys
import uuid
import shutil
import datetime
import subprocess
import urllib.parse
import webbrowser
import tempfile
import asyncio
import io 
# === MODULE NGOÀI - AUDIO, VIDEO, NGÔN NGỮ ===
import pygame
import whisper
import pytz
import srt
import pyttsx3
from pydub import AudioSegment
import edge_tts
from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException

# === PyQt6 ===
from PyQt6 import uic
from PyQt6.QtCore import (
    Qt, QTimer, QPoint, QThread, pyqtSignal, QPropertyAnimation, QRect,
    QEasingCurve, QWaitCondition, QMutex, QUrl, QAbstractAnimation
)
from PyQt6.QtGui import (
    QPainter, QPen, QPixmap, QColor, QFont, QTextCursor
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QMessageBox, QDialog, QLabel,
    QFileDialog, QColorDialog, QPushButton, QComboBox, QTextEdit,
    QProgressBar, QTableWidget, QTableWidgetItem, QGroupBox,
    QHBoxLayout, QVBoxLayout, QHeaderView, QAbstractItemView
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

# === MODULE CỤC BỘ ===
from data_json import tai_du_lieu_json, ghi_du_lieu_json  # quản lý file JSON
from flashcard_module import Flashcard  # quản lý flashcard


class ProcessingThread(QThread):

    progress_updated = pyqtSignal(int)
    log_message = pyqtSignal(str)
    processing_finished = pyqtSignal(str) # Gửi đường dẫn video đầu ra khi xong
    processing_failed = pyqtSignal(str)

    def __init__(self, video_path, output_srt_path, output_video_path, selected_language, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.output_srt_path = output_srt_path
        self.output_video_path = output_video_path
        self.selected_language = selected_language
        # Đường dẫn cho file âm thanh tạm thời
        self.temp_audio_path = os.path.join(os.path.dirname(output_video_path), "temp_audio.mp3")

    def run(self):
        try:
            self.log_message.emit("Bắt đầu xử lý video...")
            self.progress_updated.emit(5)

            # 1. Trích xuất âm thanh từ video bằng FFmpeg
            self.log_message.emit("Trích xuất âm thanh từ video bằng FFmpeg...")
            self.progress_updated.emit(10)
            
            ffmpeg_extract_audio_command = [
                "ffmpeg",
                "-i", self.video_path,
                "-q:a", "0", # Chất lượng âm thanh cao nhất (lossless)
                "-map", "a", # Chỉ trích xuất luồng âm thanh (audio stream)
                "-y", # Ghi đè file nếu đã tồn tại
                self.temp_audio_path
            ]
            
            # Sử dụng check=True để tự động nâng lỗi nếu lệnh thất bại
            subprocess.run(ffmpeg_extract_audio_command, capture_output=True, text=True, check=True)
            self.log_message.emit("Đã trích xuất âm thanh thành công.")
            self.progress_updated.emit(20)

            # 2. Nhận diện giọng nói bằng Whisper
            self.log_message.emit("Nhận diện giọng nói bằng Whisper (quá trình này có thể mất thời gian)...")
            self.progress_updated.emit(30)
            # Có thể truyền device='cuda' nếu có GPU để tăng tốc
            model = whisper.load_model("base") # Bạn có thể chọn "small", "medium", "large"
            result = model.transcribe(self.temp_audio_path, verbose=False) 
            segments = result["segments"]
            self.log_message.emit("Đã nhận diện giọng nói.")
            self.progress_updated.emit(50)

            # 3. Dịch phụ đề (nếu ngôn ngữ được chọn không phải tiếng Anh)
            translated_segments = []
            if self.selected_language != "English":
                self.log_message.emit(f"Dịch phụ đề sang {self.selected_language}...")
                self.progress_updated.emit(60)
                translator = GoogleTranslator(source='auto', target=self.map_language_to_code(self.selected_language))

                for i, segment in enumerate(segments):
                    try:
                        translated_text = translator.translate(segment["text"])
                        translated_segments.append({
                            "start": segment["start"],
                            "end": segment["end"],
                            "text": translated_text if translated_text else segment["text"] # Đảm bảo có nội dung
                        })
                    except Exception as translate_err:
                        self.log_message.emit(f"Cảnh báo: Không thể dịch đoạn '{segment['text']}'. Lỗi: {translate_err}")
                        translated_segments.append(segment) # Giữ nguyên bản gốc nếu dịch lỗi
                    # Cập nhật tiến độ dịch
                    self.progress_updated.emit(60 + int(20 * (i + 1) / len(segments)))
                self.log_message.emit("Đã dịch phụ đề.")
            else:
                self.log_message.emit("Ngôn ngữ là Tiếng Anh, không cần dịch.")
                translated_segments = segments
            self.progress_updated.emit(80)

            # 4. Tạo file SRT
            self.log_message.emit("Tạo file SRT...")
            self.progress_updated.emit(85)
            subtitles = []
            for i, segment in enumerate(translated_segments):
                # Sửa lỗi: Chuyển đổi trực tiếp giây sang timedelta
                start_timedelta = datetime.timedelta(seconds=segment["start"])
                end_timedelta = datetime.timedelta(seconds=segment["end"])
                subtitles.append(srt.Subtitle(
                    index=i+1,
                    start=start_timedelta,
                    end=end_timedelta,
                    content=segment["text"].strip()
                ))

            with open(self.output_srt_path, "w", encoding="utf-8") as f:
                f.write(srt.compose(subtitles))
            self.log_message.emit(f"Đã tạo file SRT: {os.path.basename(self.output_srt_path)}")
            self.progress_updated.emit(90)

            # 5. Ghép phụ đề vào video bằng FFmpeg
            self.log_message.emit("Ghép phụ đề vào video (quá trình này có thể mất thời gian)...")
            self.progress_updated.emit(95)

            # Đảm bảo đường dẫn tuyệt đối cho FFmpeg
            input_video_abs = os.path.abspath(self.video_path)
            output_video_abs = os.path.abspath(self.output_video_path)
            srt_abs = os.path.abspath(self.output_srt_path)

            # Quan trọng: Trên Windows, đường dẫn phụ đề trong -vf phải dùng dấu /
            # hoặc escaped backslashes. os.sep là '\' trên Windows.
            formatted_srt_path_for_ffmpeg = srt_abs.replace(os.sep, '/')

            ffmpeg_command = [
                "ffmpeg",
                "-i", input_video_abs,
                "-vf", f"subtitles='{formatted_srt_path_for_ffmpeg}'",
                "-c:v", "libx264",
                "-preset", "medium", # Có thể dùng "fast" hoặc "medium" tùy nhu cầu
                "-crf", "23", # Chất lượng (thấp hơn = tốt hơn, lớn hơn = nén nhiều hơn)
                "-c:a", "copy", # Sao chép luồng âm thanh gốc mà không mã hóa lại
                "-y", # Ghi đè file đầu ra nếu đã tồn tại
                output_video_abs
            ]
            
            process = subprocess.run(ffmpeg_command, capture_output=True, text=True, check=True)
            # Bạn có thể log stderr/stdout của ffmpeg để debug nếu cần
            # self.log_message.emit(f"FFmpeg stdout:\n{process.stdout}")
            # self.log_message.emit(f"FFmpeg stderr:\n{process.stderr}")
            self.log_message.emit("Lệnh FFmpeg đã chạy hoàn tất để ghép phụ đề.")

            self.log_message.emit("Đã ghép phụ đề vào video.")
            self.progress_updated.emit(100)
            self.processing_finished.emit(self.output_video_path)

        except subprocess.CalledProcessError as e:
            error_message = f"Lỗi FFmpeg: Lệnh: {' '.join(e.cmd)}\nStderr: {e.stderr}"
            self.log_message.emit(error_message)
            self.processing_failed.emit(error_message)
        except Exception as e:
            error_message = f"Đã xảy ra lỗi trong quá trình xử lý: {type(e).__name__}: {e}"
            self.log_message.emit(error_message)
            self.processing_failed.emit(error_message)
        finally:
            # Dọn dẹp file audio tạm thời
            if os.path.exists(self.temp_audio_path):
                os.remove(self.temp_audio_path)
                self.log_message.emit(f"Đã xóa file audio tạm thời: {os.path.basename(self.temp_audio_path)}")
                
    def format_timestamp(self, seconds):
        """Định dạng thời gian từ giây sang HH:MM:SS,ms cho file SRT. (Không còn dùng trực tiếp để tạo Subtitle)"""
        milliseconds = int((seconds - int(seconds)) * 1000)
        seconds = int(seconds)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

    def map_language_to_code(self, lang_name):
        """Ánh xạ tên ngôn ngữ sang mã ISO 639-1 cho DeepTranslator."""
        lang_map = {
            "English": "en",
            "Tiếng Việt": "vi",
            "Español": "es",
            "Français": "fr",
            "Deutsch": "de",
            "中文": "zh-CN", # Mã tiếng Trung giản thể cho GoogleTranslator
            # Thêm các ngôn ngữ khác nếu cần
        }
        return lang_map.get(lang_name, "en") # Mặc định là tiếng Anh nếu không tìm thấy

class SubtitleDialog(QDialog):
    def __init__(self):
        super().__init__()

        # Tải giao diện từ file .ui
        uic.loadUi("ui/subtitle_dialog.ui", self)
        self.setWindowTitle("Ứng Dụng Tạo Phụ Đề Video")

        self.current_video_path = None
        self.final_output_video_path = None
        self.temp_dir = "temp_subtitle_files"

        # Tạo thư mục tạm nếu chưa tồn tại
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

        self.generated_srt_path = os.path.join(self.temp_dir, "generated_output_temp.srt")
        self.temp_output_video_path = os.path.join(self.temp_dir, "vietsub_temp.mp4")

        # --- Kết nối các tín hiệu với các khe ---
        self.btn_select_video.clicked.connect(self.select_video_file)
        self.btn_process.clicked.connect(self.start_processing)
        self.btn_download.clicked.connect(self.download_output_video)

        # Cài đặt ban đầu cho các widget
        self.btn_download.setEnabled(False)
        self.btn_process.setEnabled(False)
        self.cb_language.setEnabled(False)
        self.progress_bar.setValue(0)

        # Thêm các ngôn ngữ vào ComboBox
        self.cb_language.addItems(["English", "Tiếng Việt", "Español", "Français", "Deutsch", "中文"])

        # Kiểm tra xem txt_log có tồn tại trong UI không
        if hasattr(self, 'txt_log'):
            self.txt_log.setReadOnly(True)
        else:
            print("Cảnh báo: Không tìm thấy QTextEdit với objectName 'txt_log' trong UI. Log sẽ không hiển thị trên UI.")
            # Tạo một QTextEdit tạm thời nếu không tìm thấy để tránh lỗi
            self.txt_log = QTextEdit(self)
            self.txt_log.setReadOnly(True) # Đảm bảo nó là chỉ đọc

        self.processing_thread = None

    def select_video_file(self):
        """Mở hộp thoại chọn file video."""
        file_dialog = QFileDialog()
        video_filters = "Video Files (*.mp4 *.avi *.mkv *.mov);;All Files (*)"
        file_path, _ = file_dialog.getOpenFileName(
            self,
            "Chọn Video",
            "", # Thư mục mặc định
            video_filters
        )

        if file_path:
            self.current_video_path = file_path
            QMessageBox.information(self, "Thông báo", f"Đã chọn video:\n{os.path.basename(file_path)}")
            self.log_message(f"Đã tải video: {os.path.basename(file_path)}")
            
            self.btn_process.setEnabled(True)
            self.cb_language.setEnabled(True)
            self.btn_download.setEnabled(False) # Reset nút download
            self.progress_bar.setValue(0)
            self.txt_log.clear() # Xóa log cũ
        else:
            QMessageBox.warning(self, "Cảnh báo", "Bạn chưa chọn file video nào.")
            self.current_video_path = None
            
            self.btn_process.setEnabled(False)
            self.cb_language.setEnabled(False)
            self.btn_download.setEnabled(False)
            self.log_message("Chưa có video nào được chọn.")

    def log_message(self, message):
        """Hiển thị thông báo vào QTextEdit với dấu thời gian."""
        # Kiểm tra lại một lần nữa để chắc chắn txt_log có thể được truy cập
        if hasattr(self, 'txt_log') and self.txt_log is not None:
            current_time = datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%H:%M:%S")
            self.txt_log.append(f"[{current_time}] {message}")
        else:
            print(f"Log: {message}") # In ra console nếu không có txt_log

    def start_processing(self):
        """Bắt đầu quá trình xử lý trong một luồng riêng."""
        if not self.current_video_path or not os.path.exists(self.current_video_path):
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn video trước khi xử lý.")
            return

        # Vô hiệu hóa nút để tránh xử lý trùng lặp
        self.btn_process.setEnabled(False)
        self.btn_download.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_message("Đang chuẩn bị quá trình xử lý...")

        # Khởi tạo và kết nối luồng xử lý
        self.processing_thread = ProcessingThread(
            video_path=self.current_video_path,
            output_srt_path=self.generated_srt_path,
            output_video_path=self.temp_output_video_path,
            selected_language=self.cb_language.currentText()
        )
        self.processing_thread.progress_updated.connect(self.progress_bar.setValue)
        self.processing_thread.log_message.connect(self.log_message)
        self.processing_thread.processing_finished.connect(self._on_processing_finished)
        self.processing_thread.processing_failed.connect(self._on_processing_failed)

        # Bắt đầu luồng
        self.processing_thread.start()

    def _on_processing_finished(self, output_video_path):
        """Hàm được gọi khi luồng xử lý hoàn tất thành công."""
        self.log_message("Xử lý hoàn tất!")
        self.final_output_video_path = output_video_path
        QMessageBox.information(self, "Hoàn tất", "Video đã được xử lý xong.")
        self.btn_download.setEnabled(True)
        self.btn_process.setEnabled(True) # Kích hoạt lại nút xử lý

    def _on_processing_failed(self, error_message):
        """Hàm được gọi khi luồng xử lý gặp lỗi."""
        self.log_message(f"Xử lý thất bại: {error_message}")
        QMessageBox.critical(self, "Lỗi", f"Đã xảy ra lỗi trong quá trình xử lý:\n{error_message}")
        self.btn_process.setEnabled(True) # Kích hoạt lại nút xử lý
        self.btn_download.setEnabled(False)
        self.progress_bar.setValue(0)

    def download_output_video(self):
        """Hàm này sẽ được gọi khi nút tải xuống được nhấn."""
        if self.final_output_video_path and os.path.exists(self.final_output_video_path):
            # Tạo tên file gợi ý dựa trên tên video gốc và ngôn ngữ
            base_name = os.path.basename(self.current_video_path)
            name_without_ext = os.path.splitext(base_name)[0]
            suggested_name = f"{name_without_ext}_subtitle_{self.cb_language.currentText().lower()}.mp4"

            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "Lưu Video Đã Có Phụ Đề",
                os.path.join(os.path.expanduser("~"), "Videos", suggested_name), # Thư mục mặc định là Videos của người dùng
                "Video Files (*.mp4);;All Files (*)"
            )
            if save_path:
                try:
                    shutil.copy(self.final_output_video_path, save_path)
                    QMessageBox.information(self, "Thành công", f"Video đã được lưu tại:\n{save_path}")
                    self.log_message(f"Video đã được tải xuống: {os.path.basename(save_path)}")
                except Exception as e:
                    QMessageBox.critical(self, "Lỗi Lưu File", f"Không thể lưu file: {e}")
                    self.log_message(f"Lỗi khi lưu file: {e}")
            else:
                QMessageBox.warning(self, "Cảnh báo", "Bạn chưa chọn vị trí lưu file.")
        else:
            QMessageBox.warning(self, "Cảnh báo", "Không tìm thấy video đầu ra để tải xuống. Vui lòng xử lý video trước.")

    def closeEvent(self, event):
        """
        Xử lý sự kiện khi cửa sổ ứng dụng đóng.
        Xóa tất cả các file tạm và thư mục tạm.
        """
        print("\nỨng dụng đang đóng. Đang dọn dẹp các file tạm...")
        self.log_message("Đang dọn dẹp các file tạm...")

        # Đảm bảo luồng đang chạy được dừng
        if self.processing_thread and self.processing_thread.isRunning():
            self.processing_thread.quit()
            self.processing_thread.wait() # Chờ luồng kết thúc
            self.log_message("Luồng xử lý đã được dừng.")

        # Xóa thư mục tạm thời
        if os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                print(f"Đã xóa thư mục tạm: {self.temp_dir}")
                self.log_message(f"Đã xóa thư mục tạm: {self.temp_dir}")
            except OSError as e:
                print(f"Lỗi khi xóa thư mục tạm '{self.temp_dir}': {e}")
                self.log_message(f"Lỗi khi xóa thư mục tạm: {e}")

        # Hỏi người dùng có muốn xóa video gốc đã tải lên không
        if self.current_video_path and os.path.exists(self.current_video_path):
            reply = QMessageBox.question(
                self,
                "Xác nhận đóng ứng dụng",
                f"Bạn có muốn xóa file video gốc đã tải lên:\n'{os.path.basename(self.current_video_path)}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    os.remove(self.current_video_path)
                    print(f"  Đã xóa video đầu vào: {self.current_video_path}")
                    self.log_message(f"Đã xóa video đầu vào: {self.current_video_path}")
                except OSError as e:
                    print(f"Lỗi khi xóa video đầu vào '{self.current_video_path}': {e}")
                    self.log_message(f"Lỗi khi xóa video đầu vào: {e}")
            else:
                print("  Không xóa video đầu vào theo yêu cầu người dùng.")
                self.log_message("Không xóa video đầu vào.")

        event.accept()

VOICE_MAP = {
    "en": {
        "edge_tts": "en-US-JennyNeural",
        "pyttsx3": "english"
    },
    "vi": {
        "edge_tts": "vi-VN-HoaiMyNeural",
        "pyttsx3": "vietnamese"
    },
    "fr": {
        "edge_tts": "fr-FR-DeniseNeural",
        "pyttsx3": "french"
    },
    "es": {
        "edge_tts": "es-ES-ElviraNeural",
        "pyttsx3": "spanish"
    },
    "de": {
        "edge_tts": "de-DE-KatjaNeural",
        "pyttsx3": "german"
    },
    "ko": {
        "edge_tts": "ko-KR-SunHiNeural",
        "pyttsx3": "korean"
    }
}

DEFAULT_EDGE_TTS_VOICE = "en-US-JennyNeural"
DEFAULT_PYTTSX3_VOICE = "english"


# --- Lớp SpeakService để quản lý việc phát âm thanh ---
class SpeakService(QThread):
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self._is_running = True
        self._wait_condition = QWaitCondition()
        self._mutex = QMutex()
        self.current_edge_tts_voice = DEFAULT_EDGE_TTS_VOICE
        self.current_pyttsx3_voice = DEFAULT_PYTTSX3_VOICE

    def run(self):
        detected_lang = None
        try:
            if self.text.strip():
                detected_lang = detect(self.text)
        except LangDetectException:
            detected_lang = None

        if detected_lang and detected_lang in VOICE_MAP:
            self.current_edge_tts_voice = VOICE_MAP[detected_lang]["edge_tts"]
            self.current_pyttsx3_voice = VOICE_MAP[detected_lang]["pyttsx3"]
        else:
            self.current_edge_tts_voice = DEFAULT_EDGE_TTS_VOICE
            self.current_pyttsx3_voice = DEFAULT_PYTTSX3_VOICE
        
        try:
            temp_mp3_path = None
            async def get_audio_from_edge_tts():
                nonlocal temp_mp3_path
                communicate = edge_tts.Communicate(text=self.text, voice=self.current_edge_tts_voice)
                audio_bytes = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes += chunk["data"]
                
                if not audio_bytes:
                    raise Exception("No audio was received from Edge TTS.")
                
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_mp3:
                    temp_mp3.write(audio_bytes)
                    temp_mp3_path = temp_mp3.name
                
                return audio_bytes

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            edge_audio_bytes = loop.run_until_complete(get_audio_from_edge_tts())
            loop.close()

            if not pygame.mixer.get_init():
                pygame.mixer.init()
            
            audio = AudioSegment.from_file(io.BytesIO(edge_audio_bytes), format="mp3")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_wav:
                audio.export(temp_wav.name, format="wav")
                pygame.mixer.music.load(temp_wav.name)
            
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy() and self._is_running:
                self._mutex.lock()
                self._wait_condition.wait(self._mutex, 100)
                self._mutex.unlock()

            pygame.mixer.music.stop()

            if temp_mp3_path and os.path.exists(temp_mp3_path):
                os.remove(temp_mp3_path)
            if temp_wav.name and os.path.exists(temp_wav.name):
                os.remove(temp_wav.name)

        except Exception as e:
            # Fallback sang pyttsx3
            try:
                engine = pyttsx3.init()
                
                if self.current_pyttsx3_voice:
                    voices = engine.getProperty('voices')
                    found_voice = False
                    for voice_obj in voices:
                        if self.current_pyttsx3_voice.lower() in voice_obj.name.lower() or \
                           self.current_pyttsx3_voice.lower() in voice_obj.id.lower():
                            engine.setProperty('voice', voice_obj.id)
                            found_voice = True
                            break
                    if not found_voice:
                        pass
                
                engine.say(self.text)
                engine.runAndWait()
            except Exception as e_pyttsx3:
                self.error.emit(f"Không thể phát âm thanh: Edge TTS lỗi ({e}), Pyttsx3 cũng lỗi ({e_pyttsx3})")
        
        self.finished.emit()

    def stop_speaking(self):
        self._mutex.lock()
        self._is_running = False
        self._wait_condition.wakeAll()
        self._mutex.unlock()
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()

class NguoiDung:
    def __init__(self, ten_nguoi_dung, mat_khau, email, dob=None, phone=None, profile_picture_path=None, study_methods=None):
        self.ten_nguoi_dung = ten_nguoi_dung
        self.mat_khau = mat_khau
        self.email = email
        self.dob = dob
        self.phone = phone
        self.profile_picture_path = profile_picture_path
        self.study_methods = study_methods if study_methods is not None else []

class CoSoDuLieuNguoiDung:
    def __init__(self, user_file="user.json"):
        self.user_file = user_file
        self.danh_sach_nguoi_dung = []
        self.du_lieu_nguoi_dung = tai_du_lieu_json(self.user_file)
        self.danh_sach_ten_nguoi_dung = self._tai_danh_sach_ten()
        
        # Đảm bảo dữ liệu người dùng có các trường cần thiết
        idx = 0
        while idx < len(self.du_lieu_nguoi_dung):
            user = self.du_lieu_nguoi_dung[idx]
            if "flashcards" not in user:
                user["flashcards"] = []
            if "id" not in user:
                user["id"] = str(uuid.uuid4())
            if "study_methods" not in user: # Thêm trường study_methods
                user["study_methods"] = []
            idx += 1
        ghi_du_lieu_json(self.user_file, self.du_lieu_nguoi_dung)

    def _tai_danh_sach_ten(self):
        return [item["username"] for item in self.du_lieu_nguoi_dung]

    def xac_thuc_dang_nhap(self, email, mat_khau, ten):
        for du_lieu in self.du_lieu_nguoi_dung:
            if (du_lieu.get("email") == email and
                du_lieu.get("password") == mat_khau and
                du_lieu.get("username") == ten):
                return du_lieu
        return None

    def tai_du_lieu(self):
        self.danh_sach_nguoi_dung = []
        for du_lieu in self.du_lieu_nguoi_dung:
            nguoi_dung = NguoiDung(
                ten_nguoi_dung=du_lieu.get("username"),
                mat_khau=du_lieu.get("password"),
                email=du_lieu.get("email"),
                dob=du_lieu.get("dob"),
                phone=du_lieu.get("phone"),
                profile_picture_path=du_lieu.get("profile_picture_path"),
                study_methods=du_lieu.get("study_methods", [])
            )
            self.danh_sach_nguoi_dung.append(nguoi_dung)

    def cap_nhat_du_lieu(self, username, du_lieu_moi):
        updated = False
        idx = 0
        while idx < len(self.du_lieu_nguoi_dung):
            user_data = self.du_lieu_nguoi_dung[idx]
            if user_data.get("username") == username:
                self.du_lieu_nguoi_dung[idx] = du_lieu_moi
                updated = True
                break
            idx += 1
        
        if updated:
            ghi_du_lieu_json(self.user_file, self.du_lieu_nguoi_dung)
            return True
        return False

    def luu_du_lieu(self, du_lieu_moi):
        if "id" not in du_lieu_moi:
            du_lieu_moi["id"] = str(uuid.uuid4())
        if "flashcards" not in du_lieu_moi:
            du_lieu_moi["flashcards"] = []
        if "study_methods" not in du_lieu_moi: # Đảm bảo có trường study_methods khi lưu mới
            du_lieu_moi["study_methods"] = []

        nguoi_dung_moi = NguoiDung(
            ten_nguoi_dung=du_lieu_moi["username"],
            mat_khau=du_lieu_moi["password"],
            email=du_lieu_moi["email"],
            dob=du_lieu_moi.get("dob"),
            phone=du_lieu_moi.get("phone"),
            profile_picture_path=du_lieu_moi.get("profile_picture_path"),
            study_methods=du_lieu_moi.get("study_methods", [])
        )
        self.danh_sach_nguoi_dung.append(nguoi_dung_moi)
        self.du_lieu_nguoi_dung.append(du_lieu_moi)
        ghi_du_lieu_json(self.user_file, self.du_lieu_nguoi_dung)

    def lay_flashcards_cua_nguoi_dung(self, user_id):
        for user_data in self.du_lieu_nguoi_dung:
            if user_data.get("id") == user_id:
                flashcard_dicts = user_data.get("flashcards", [])
                flashcards = [Flashcard(
                    card_id=d.get("id"),
                    front_text=d.get("front_text", ""),
                    back_text=d.get("back_text", ""),
                    image_front_path=d.get("image_front_path"),
                    image_back_path=d.get("image_back_path"),
                    status=d.get("status", "new")
                ) for d in flashcard_dicts]
                return flashcards
        return []

    def cap_nhat_flashcards_cho_nguoi_dung(self, user_id, flashcards):
        i_index = 0
        while i_index < len(self.du_lieu_nguoi_dung):
            user_data = self.du_lieu_nguoi_dung[i_index]
            if user_data.get("id") == user_id:
                self.du_lieu_nguoi_dung[i_index]["flashcards"] = [card.to_dict() for card in flashcards]
                ghi_du_lieu_json(self.user_file, self.du_lieu_nguoi_dung)
                return True
            i_index += 1
        return False

    def them_phuong_phap_cho_nguoi_dung(self, user_id, ten_phuong_phap, mo_ta, thoi_gian_khuyen_nghi_giay):
        for user_data in self.du_lieu_nguoi_dung:
            if user_data.get("id") == user_id:
                if "study_methods" not in user_data:
                    user_data["study_methods"] = []

                # Kiểm tra xem phương pháp đã tồn tại chưa để tránh trùng lặp
                for method in user_data["study_methods"]:
                    if method["name"].lower() == ten_phuong_phap.lower(): # So sánh không phân biệt hoa thường
                        return False # Phương pháp đã tồn tại

                user_data["study_methods"].append({
                    "name": ten_phuong_phap,
                    "description": mo_ta,
                    "recommended_time": thoi_gian_khuyen_nghi_giay
                })
                ghi_du_lieu_json(self.user_file, self.du_lieu_nguoi_dung)
                return True
        return False

    def lay_phuong_phap_cua_nguoi_dung(self, user_id):
        for user_data in self.du_lieu_nguoi_dung:
            if user_data.get("id") == user_id:
                return user_data.get("study_methods", [])
        return []

NGON_NGU_DICH_THUAT = {
    'af': 'afrikaans', 'sq': 'albanian', 'am': 'amharic', 'ar': 'arabic',
    'hy': 'armenian', 'az': 'azerbaijani', 'eu': 'basque', 'be': 'belarusian',
    'bn': 'bengali', 'bs': 'bosnian', 'bg': 'bulgarian', 'ca': 'catalan',
    'ceb': 'cebuano', 'ny': 'chichewa', 'zh-cn': 'chinese (simplified)',
    'zh-tw': 'chinese (traditional)', 'co': 'corsican', 'hr': 'croatian',
    'cs': 'czech', 'da': 'danish', 'nl': 'dutch', 'en': 'english',
    'eo': 'esperanto', 'et': 'estonian', 'tl': 'filipino', 'fi': 'finnish',
    'fr': 'french', 'fy': 'frisian', 'gl': 'galician', 'ka': 'georgian',
    'de': 'german', 'el': 'greek', 'gu': 'gujarati', 'ht': 'haitian creole',
    'ha': 'hausa', 'haw': 'hawaiian', 'iw': 'hebrew', 'hi': 'hindi',
    'hmn': 'hmong', 'hu': 'hungarian', 'is': 'icelandic', 'ig': 'igbo',
    'id': 'indonesian', 'ga': 'irish', 'it': 'italian', 'ja': 'japanese',
    'jw': 'javanese', 'kn': 'kannada', 'kk': 'kazakh', 'km': 'khmer',
    'ko': 'korean', 'ku': 'kurdish (kurmanji)', 'ky': 'kyrgyz', 'lo': 'lao',
    'la': 'latin', 'lv': 'latvian', 'lt': 'lithuanian', 'lb': 'luxembourgish',
    'mk': 'macedonian', 'mg': 'malagasy', 'ms': 'malay', 'ml': 'malayalam',
    'mt': 'maltese', 'mi': 'maori', 'mr': 'marathi', 'mn': 'mongolian',
    'my': 'myanmar (burmese)', 'ne': 'nepali', 'no': 'norwegian', 'ps': 'pashto',
    'fa': 'persian', 'pl': 'polish', 'pt': 'portuguese', 'pa': 'punjabi',
    'ro': 'romanian', 'ru': 'russian', 'sm': 'samoan', 'gd': 'scots gaelic',
    'sr': 'serbian', 'st': 'sesotho', 'sn': 'shona', 'sd': 'sindhi',
    'si': 'sinhala', 'sk': 'slovak', 'sl': 'slovenian', 'so': 'somali',
    'es': 'spanish', 'su': 'sundanese', 'sw': 'swahili', 'sv': 'swedish',
    'tg': 'tajik', 'ta': 'tamil', 'te': 'telugu', 'th': 'thai', 'tr': 'turkish',
    'uk': 'ukrainian', 'ur': 'urdu', 'uz': 'uzbek', 'vi': 'vietnamese',
    'cy': 'welsh', 'xh': 'xhosa', 'yi': 'yiddish', 'yo': 'yoruba', 'zu': 'zulu'
}

PHUONG_PHAP_HOC = {
    "Pomodoro": ("Pomodoro", "Học 25 phút, nghỉ 5 phút. Sau 4 lần, nghỉ 15-30 phút."),
    "Active Recall": ("Active Recall", "Tự kiểm tra mà không nhìn tài liệu."),
    "Spaced Repetition": ("Spaced Repetition", "Ôn tập kiến thức theo khoảng cách tăng dần."),
    "Mind Mapping": ("Mind Mapping", "Tóm tắt nội dung bằng sơ đồ tư duy."),
    "Feynman Technique": ("Feynman Technique", "Giải thích lại bằng ngôn ngữ đơn giản."),
    "Học bằng cách dạy": ("Học bằng cách dạy", "Dạy lại người khác để hiểu sâu hơn."),
    "SQ3R": ("SQ3R", "Đọc lướt, đặt câu hỏi, đọc kỹ, nhắc lại và ôn tập."),
    "Cornell Note-Taking": ("Cornell Note-Taking", "Ghi chú thành 3 phần: từ khoá, nội dung, tóm tắt."),
    "Mnemonics": ("Mnemonics", "Dùng hình ảnh, câu chuyện, chữ viết tắt để nhớ lâu."),
    "Interleaving": ("Interleaving", "Học đan xen nhiều môn hoặc chủ đề khác nhau."),
    "Project-Based Learning": ("Project-Based Learning", "Áp dụng kiến thức bằng cách thực hiện dự án."),
    "Học qua video": ("Học qua video", "Xem video học tập trên YouTube hoặc nền tảng khác."),
    "Học bằng flashcard": ("Học bằng flashcard", "Dùng thẻ ghi nhớ để ôn tập nhanh và hiệu quả."),
    "Phương pháp Leitner": ("Phương pháp Leitner", "Lặp lại thẻ ghi nhớ theo cấp độ."),
    "Học qua âm nhạc": ("Học qua âm nhạc", "Nghe nhạc không lời giúp tăng tập trung."),
    "Phương pháp PQ4R": ("Phương pháp PQ4R", "Preview, Question, Read, Reflect, Recite, Review."),
    "Flowtime": ("Flowtime", "Học liên tục khi tập trung, nghỉ khi cảm thấy mệt.")
}

THOI_GIAN_HOC = {
    "Pomodoro": 1500, # 25 phút
    "Flowtime": 1800, # 30 phút
    "Active Recall": 1800, # 30 phút
    "Spaced Repetition": 2700, # 45 phút
    "Feynman Technique": 900, # 15 phút
    "SQ3R": 2400, # 40 phút
    "Mind Mapping": 1200, # 20 phút
    "Cornell Note-Taking": 2100, # 35 phút
    "Mnemonics": 600, # 10 phút
    "Học bằng cách dạy": 1800, # 30 phút
    "Interleaving": 3600, # 60 phút
    "Project-Based Learning": 5400, # 90 phút
    "Học qua video": 2700, # 45 phút
    "Học bằng flashcard": 1200, # 20 phút
    "Phương pháp Leitner": 1800, # 30 phút
    "Học qua âm nhạc": 1800, # 30 phút (đã sửa từ 3600)
    "Phương pháp PQ4R": 3000, # 50 phút
    "Phương pháp test": 1,
}

class FlashcardXacNhan(QDialog):
    """
    Cửa sổ pop-up xác nhận hành động.
    """
    def __init__(self, message, parent=None):
        super().__init__(parent)
        self.ui = uic.loadUi("ZenTask/ui/Flashcard_Confirm_Popup.ui", self)
        self.setModal(True)

        self.ui.labelMessage.setText(message)

        self.ui.pushButtonConfirm.clicked.connect(self.accept)
        self.ui.pushButtonCancelConfirm.clicked.connect(self.reject)

class FlashcardThemSua(QDialog):
    """
    Cửa sổ pop-up thêm/sửa Flashcard.
    """
    card_saved = pyqtSignal(Flashcard)

    def __init__(self, flashcard_to_edit=None, parent=None):
        super().__init__(parent)
        self.ui = uic.loadUi("ui/Flashcard_AddEdit_Popup.ui", self)
        
        self.flashcard_to_edit = flashcard_to_edit
        self.edited_flashcard = None

        self.temp_image_front_path = None # Full path of selected image for front
        self.temp_image_back_path = None  # Full path of selected image for back

        self.ui.labelAddEditTitle.setText("Chỉnh sửa Flashcard" if self.flashcard_to_edit else "Thêm Flashcard Mới")
        
        self.ui.textEditFront.setText(self.flashcard_to_edit.front_text if self.flashcard_to_edit else "")
        self.ui.textEditBack.setText(self.flashcard_to_edit.back_text if self.flashcard_to_edit else "")
        
        if self.flashcard_to_edit:
            self._display_image_in_textedit(self.flashcard_to_edit.image_front_path, self.ui.textEditFront)
            self._display_image_in_textedit(self.flashcard_to_edit.image_back_path, self.ui.textEditBack)

        self.ui.pushButtonSave.clicked.connect(self._validate_and_save)
        self.ui.pushButtonCancel.clicked.connect(self.close)

        self.ui.BTNaddimage_front.clicked.connect(lambda: self._load_and_preview_image("front"))
        self.ui.BTNaddimage_back.clicked.connect(lambda: self._load_and_preview_image("back"))

    def _display_image_in_textedit(self, image_relative_path, text_edit_widget):
        """Hiển thị ảnh từ thư mục flashcard_images vào QTextEdit."""
        if image_relative_path:
            full_path = os.path.join("data", "flashcard_images", image_relative_path)
            if os.path.exists(full_path):
                image_url = QUrl.fromLocalFile(full_path).toString()
                text_edit_widget.moveCursor(QTextCursor.MoveOperation.End) # ĐÃ SỬA LỖI TẠI ĐÂY
                text_edit_widget.insertHtml(f"<img src=\"{image_url}\" /><br>")

    def _load_and_preview_image(self, side):
        """Mở hộp thoại chọn ảnh, lưu đường dẫn tạm thời và hiển thị xem trước trong QTextEdit."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh",
            "",
            "Tệp ảnh (*.png *.jpg *.jpeg *.bmp *.gif)"
        )

        if file_path:
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                if side == "front":
                    self.temp_image_front_path = file_path
                    text_edit_widget = self.ui.textEditFront
                elif side == "back":
                    self.temp_image_back_path = file_path
                    text_edit_widget = self.ui.textEditBack
                
                image_url = QUrl.fromLocalFile(file_path).toString()
                text_edit_widget.moveCursor(QTextCursor.MoveOperation.End) # ĐÃ SỬA LỖI TẠI ĐÂY
                text_edit_widget.insertHtml(f"<img src=\"{image_url}\" /><br>")
            else:
                QMessageBox.warning(self, "Lỗi", "Không thể tải ảnh đã chọn.")
    
    def _copy_image_to_storage(self, source_path):
        """
        Sao chép ảnh từ đường dẫn nguồn vào thư mục 'data/flashcard_images'
        và trả về tên file duy nhất. Tránh sao chép lại nếu đã có.
        """
        if not source_path:
            return None
        
        flashcard_image_dir = os.path.join("data", "flashcard_images")
        
        abs_source_path = os.path.abspath(source_path)
        abs_flashcard_image_dir = os.path.abspath(flashcard_image_dir)

        if abs_source_path.startswith(abs_flashcard_image_dir) and os.path.exists(source_path):
            return os.path.basename(source_path)

        if not os.path.exists(source_path):
            return None

        os.makedirs(flashcard_image_dir, exist_ok=True)
        file_name = os.path.basename(source_path)
        unique_file_name = f"{uuid.uuid4().hex}_{file_name}"
        destination_path = os.path.join(flashcard_image_dir, unique_file_name)

        try:
            shutil.copy2(source_path, destination_path)
            return unique_file_name
        except Exception:
            QMessageBox.critical(self, "Lỗi sao chép", "Không thể sao chép tệp ảnh. Vui lòng kiểm tra quyền truy cập.")
            return None

    def _validate_and_save(self):
        front_text = self.ui.textEditFront.toPlainText().strip()
        back_text = self.ui.textEditBack.toPlainText().strip()

        has_front_content = bool(front_text) or bool(self.temp_image_front_path) or \
            (self.flashcard_to_edit and self.flashcard_to_edit.image_front_path)
        has_back_content = bool(back_text) or bool(self.temp_image_back_path) or \
            (self.flashcard_to_edit and self.flashcard_to_edit.image_back_path)

        if not has_front_content:
            QMessageBox.warning(self, "Lỗi", "Mặt trước của thẻ không được để trống hoặc chưa có ảnh.")
            return

        if not has_back_content:
            QMessageBox.warning(self, "Lỗi", "Mặt sau của thẻ không được để trống hoặc chưa có ảnh.")
            return

        image_front_name = self._copy_image_to_storage(self.temp_image_front_path)
        image_back_name = self._copy_image_to_storage(self.temp_image_back_path)

        # Nếu đang chỉnh sửa và có ảnh mới -> xóa ảnh cũ
        if self.flashcard_to_edit:
            # Mặt trước
            if self.temp_image_front_path:
                old_front_path = os.path.join("data", "flashcard_images", self.flashcard_to_edit.image_front_path)
                if os.path.exists(old_front_path):
                    try:
                        os.remove(old_front_path)
                    except Exception as e:
                        print(f"Lỗi khi xóa ảnh cũ mặt trước: {e}")

            # Mặt sau
            if self.temp_image_back_path:
                old_back_path = os.path.join("data", "flashcard_images", self.flashcard_to_edit.image_back_path)
                if os.path.exists(old_back_path):
                    try:
                        os.remove(old_back_path)
                    except Exception as e:
                        print(f"Lỗi khi xóa ảnh cũ mặt sau: {e}")

        # Nếu ảnh mới không có (người dùng không đổi) -> giữ nguyên ảnh cũ
        if not image_front_name and self.flashcard_to_edit:
            image_front_name = self.flashcard_to_edit.image_front_path

        if not image_back_name and self.flashcard_to_edit:
            image_back_name = self.flashcard_to_edit.image_back_path

        # Tạo hoặc cập nhật flashcard
        if self.flashcard_to_edit:
            self.flashcard_to_edit.front_text = front_text
            self.flashcard_to_edit.back_text = back_text
            self.flashcard_to_edit.image_front_path = image_front_name
            self.flashcard_to_edit.image_back_path = image_back_name
            self.edited_flashcard = self.flashcard_to_edit
        else:
            self.edited_flashcard = Flashcard(
                front_text=front_text,
                back_text=back_text,
                image_front_path=image_front_name,
                image_back_path=image_back_name
            )

        self.card_saved.emit(self.edited_flashcard)
        self.close()

class FlashcardQuanLy(QDialog):
    """
    Cửa sổ pop-up Quản lý Flashcard và Thống kê.
    """
    def __init__(self, user_id, db_instance, parent=None):
        super().__init__(parent)
        self.ui = uic.loadUi("ui/Flashcard_Main_Popup.ui", self)

        self.user_id = user_id
        self.db = db_instance
        self.flashcards = []
        self.filtered_flashcards = []

        self.setup_table_widget()
        self.setup_filter_combobox()
        self.load_flashcards()
        self.update_statistics()

        self.ui.pushButtonAdd.clicked.connect(self.add_flashcard)
        self.ui.pushButtonEdit.clicked.connect(self.edit_flashcard)
        self.ui.pushButtonDelete.clicked.connect(self.delete_flashcard)
        self.ui.pushButtonCloseMain.clicked.connect(self.close)
        self.ui.pushButtonSearch.clicked.connect(self.perform_search)
        self.ui.lineEditSearch.returnPressed.connect(self.perform_search)
        self.ui.pushButtonStudy.clicked.connect(self.open_study_session)
        self.setup_study_mode_combobox()

        self.add_edit_popup_instance = None
        self.study_popup_instance = None

    def setup_table_widget(self):
        self.ui.tableWidgetFlashcards.setColumnCount(3)
        self.ui.tableWidgetFlashcards.setHorizontalHeaderLabels(["Mặt trước", "Mặt sau", "Trạng thái"])
        self.ui.tableWidgetFlashcards.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ui.tableWidgetFlashcards.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.ui.tableWidgetFlashcards.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.ui.tableWidgetFlashcards.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers) 
    
    def load_flashcards(self):
        self.flashcards = self.db.lay_flashcards_cua_nguoi_dung(self.user_id)
        self.filter_flashcards()
    
    def setup_study_mode_combobox(self):
        self.ui.comboBoxStudyMode.clear()
        self.ui.comboBoxStudyMode.addItem("Flashcard mới và chưa thuộc", "new_and_unknown")
        self.ui.comboBoxStudyMode.addItem("Tất cả flashcard", "all")
        self.ui.comboBoxStudyMode.setCurrentIndex(0)

    def setup_filter_combobox(self):
        if hasattr(self.ui, 'comboBoxFilterStatus'):
            self.ui.comboBoxFilterStatus.clear()
            self.ui.comboBoxFilterStatus.addItem("Tất cả", "all")
            self.ui.comboBoxFilterStatus.addItem("Mới", "new")
            self.ui.comboBoxFilterStatus.addItem("Đã thuộc", "known")
            self.ui.comboBoxFilterStatus.addItem("Chưa thuộc", "unknown")
            self.ui.comboBoxFilterStatus.setCurrentIndex(0)
            self.ui.comboBoxFilterStatus.currentIndexChanged.connect(self.filter_flashcards)
        else:
            QMessageBox.warning(self, "Lỗi UI", "Không tìm thấy 'comboBoxFilterStatus' trong Flashcard_Main_Popup.ui. Vui lòng kiểm tra file UI.")

    def display_flashcards(self):
        self.ui.tableWidgetFlashcards.setRowCount(len(self.filtered_flashcards))
        row_index = 0
        while row_index < len(self.filtered_flashcards):
            card = self.filtered_flashcards[row_index]
            self.ui.tableWidgetFlashcards.setItem(row_index, 0, QTableWidgetItem(card.front_text))
            self.ui.tableWidgetFlashcards.setItem(row_index, 1, QTableWidgetItem(card.back_text))
            self.ui.tableWidgetFlashcards.setItem(row_index, 2, QTableWidgetItem(card.status))
            self.ui.tableWidgetFlashcards.item(row_index, 0).setData(Qt.ItemDataRole.UserRole, card.id)
            row_index += 1

    def update_statistics(self):
        total_cards = len(self.flashcards)
        known_cards = sum(1 for card in self.flashcards if card.status == "known")
        unknown_cards = sum(1 for card in self.flashcards if card.status == "unknown")
        new_cards = sum(1 for card in self.flashcards if card.status == "new")

        self.ui.labelValueTotalCards.setText(str(total_cards))
        self.ui.labelValueKnownCards.setText(str(known_cards))
        self.ui.labelValueUnknownCards.setText(str(unknown_cards))
        self.ui.labelValueNewCards.setText(str(new_cards))

    def perform_search(self):
        search_text = self.ui.lineEditSearch.text().strip().lower()
        self.filter_flashcards(search_text=search_text)

    def filter_flashcards(self, index=None, search_text=None):
        if hasattr(self.ui, 'comboBoxFilterStatus'):
            selected_status = self.ui.comboBoxFilterStatus.currentData()
        else:
            selected_status = "all"

        temp_filtered_cards = list(self.flashcards)

        if selected_status != "all":
            temp_filtered_cards = [card for card in temp_filtered_cards if card.status == selected_status]

        if search_text is None:
            search_text = self.ui.lineEditSearch.text().strip().lower()
        
        if search_text:
            temp_filtered_cards = [
                card for card in temp_filtered_cards
                if search_text in card.front_text.lower() or search_text in card.back_text.lower()
            ]
        
        self.filtered_flashcards = temp_filtered_cards
        self.display_flashcards()

    def add_flashcard(self):
        self.add_edit_popup_instance = FlashcardThemSua(parent=self)
        self.add_edit_popup_instance.card_saved.connect(self._handle_card_saved)
        self.add_edit_popup_instance.show()

    def _handle_card_saved(self, new_card):
        if new_card:
            if new_card.id in [card.id for card in self.flashcards]:
                i_index = 0
                while i_index < len(self.flashcards):
                    if self.flashcards[i_index].id == new_card.id:
                        self.flashcards[i_index] = new_card
                        break
                    i_index += 1
                QMessageBox.information(self, "Thành công", "Flashcard đã được cập nhật.")
            else:
                self.flashcards.append(new_card)
                QMessageBox.information(self, "Thành công", "Flashcard đã được thêm mới.")
            
            self.db.cap_nhat_flashcards_cho_nguoi_dung(self.user_id, self.flashcards)
            self.load_flashcards()
            self.update_statistics()

    def edit_flashcard(self):
        selected_items = self.ui.tableWidgetFlashcards.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn một flashcard để sửa.")
            return

        selected_row = selected_items[0].row()
        card_id = self.ui.tableWidgetFlashcards.item(selected_row, 0).data(Qt.ItemDataRole.UserRole)
        
        card_to_edit = next((card for card in self.flashcards if card.id == card_id), None)

        if card_to_edit:
            self.add_edit_popup_instance = FlashcardThemSua(flashcard_to_edit=card_to_edit, parent=self)
            self.add_edit_popup_instance.card_saved.connect(self._handle_card_saved)
            self.add_edit_popup_instance.show()
        else:
            QMessageBox.critical(self, "Lỗi", "Không tìm thấy flashcard để sửa.")

    def delete_flashcard(self):
        selected_rows = sorted(list(set(item.row() for item in self.ui.tableWidgetFlashcards.selectedItems())), reverse=True)
        if not selected_rows:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn ít nhất một flashcard để xóa.")
            return

        confirm_popup = FlashcardXacNhan(
            f"Bạn có chắc chắn muốn xóa {len(selected_rows)} flashcard đã chọn không?", parent=self
        )
        if confirm_popup.exec() == QDialog.DialogCode.Accepted:
            deleted_count = 0
            for row in selected_rows:
                card_id = self.ui.tableWidgetFlashcards.item(row, 0).data(Qt.ItemDataRole.UserRole)
                self.flashcards = [card for card in self.flashcards if card.id != card_id]
                deleted_count += 1
            
            self.db.cap_nhat_flashcards_cho_nguoi_dung(self.user_id, self.flashcards)
            self.load_flashcards()
            self.update_statistics()
            QMessageBox.information(self, "Thành công", f"Đã xóa {deleted_count} flashcard.")

    def open_study_session(self):
        if not self.flashcards:
            QMessageBox.information(self, "Thông báo", "Không có flashcard nào để học. Vui lòng thêm flashcard trước.")
            return

        selected_mode_data = self.ui.comboBoxStudyMode.currentData()
        cards_to_study = []

        if selected_mode_data == "new_and_unknown":
            cards_to_study = [card for card in self.flashcards if card.status in ["new", "unknown"]]
            if not cards_to_study:
                QMessageBox.information(self, "Thông báo", "Tất cả flashcard đã được học hoặc không có thẻ mới/chưa biết.")
                return
        elif selected_mode_data == "all":
            cards_to_study = list(self.flashcards)
            if not cards_to_study:
                QMessageBox.information(self, "Thông báo", "Không có flashcard nào trong bộ sưu tập của bạn.")
                return

        self.study_popup_instance = FlashcardHoc(cards_to_study, self.user_id, self.db, self)
        self.study_popup_instance.finished.connect(self._handle_study_finished)
        self.study_popup_instance.show()

    def _handle_study_finished(self):
        self.load_flashcards()
        self.update_statistics()

class FlashcardHoc(QDialog):
    """
    Cửa sổ pop-up Học Flashcard.
    """
    def __init__(self, flashcards_to_study, user_id, db_instance, parent=None):
        super().__init__(parent)
        self.ui = uic.loadUi("ui/Flashcard_Study_Popup.ui", self)
        self.anim1 = None # Khởi tạo các biến animation là None
        self.anim2 = None
        self.original_card_geometry = None # Đảm bảo biến này được khởi tạo
        self.flashcards = flashcards_to_study
        self.user_id = user_id
        self.db = db_instance
        self.current_card_index = 0
        self.is_front_side = True
        self.original_card_geometry = None
        self.speak_thread = None

        if not self.flashcards:
            QMessageBox.information(self, "Thông báo", "Không có thẻ nào để học.")
            self.close()
            return

        self.setup_card_display()
        self.show_current_card()

        self.ui.pushButtonFlip.clicked.connect(self.flip_card_animation)
        self.ui.pushButtonPrevious.clicked.connect(self.show_previous_card)
        self.ui.pushButtonNext.clicked.connect(self.show_next_card)
        self.ui.pushButtonKnown.clicked.connect(lambda: self.evaluate_card("known"))
        self.ui.pushButtonUnknown.clicked.connect(lambda: self.evaluate_card("unknown"))
        self.ui.pushButtonCloseStudy.clicked.connect(self.close)
        
        if hasattr(self.ui, 'pushButtonSpeak'):
            self.ui.pushButtonSpeak.clicked.connect(self.speak_current_text)

    def setup_card_display(self):
        self.ui.label_flashcard.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ui.label_flashcard.setWordWrap(True)
        self.ui.label_flashcard.setTextFormat(Qt.TextFormat.RichText)

    def show_current_card(self):
        if not self.flashcards:
            self.ui.label_flashcard.setText("Không có thẻ nào để học.")
            self.ui.pushButtonFlip.setEnabled(False)
            self.ui.pushButtonKnown.setEnabled(False)
            self.ui.pushButtonUnknown.setEnabled(False)
            self.ui.pushButtonNext.setEnabled(False)
            self.ui.pushButtonPrevious.setEnabled(False)
            if hasattr(self.ui, 'pushButtonSpeak'): 
                self.ui.pushButtonSpeak.setEnabled(False)
            return

        current_card = self.flashcards[self.current_card_index]
        text_to_display = current_card.front_text if self.is_front_side else current_card.back_text
        image_path = current_card.image_front_path if self.is_front_side else current_card.image_back_path

        # Tạo nội dung HTML để hiển thị cả văn bản và ảnh
        html_content = ""
        if text_to_display and not image_path:
            html_content += f"<div style='font-size: 35pt; margin-bottom: 10px;'>{text_to_display}</div>"
        elif text_to_display and image_path:
            html_content += f"<div style='font-size: 20pt; margin-bottom: 10px;'>{text_to_display}</div>"
    
            full_image_path = os.path.join("data", "flashcard_images", image_path)
            if os.path.exists(full_image_path):
                image_url = QUrl.fromLocalFile(full_image_path).toString()
                html_content += f"<img src='{image_url}' style='max-width: 100%; max-height: 300px;'>"
            else:
                html_content += "<div style='color: red;'>[Không tìm thấy ảnh]</div>"
        
        # Hiển thị nội dung HTML
        self.ui.label_flashcard.setText(html_content)
        
        self.update_card_count_label()
        self.update_navigation_buttons_state()
            
        self.update_card_count_label()

    def update_card_count_label(self):
        """Cập nhật nhãn hiển thị số lượng thẻ hiện tại."""
        if hasattr(self.ui, 'labelCardCount'):
            total_cards = len(self.flashcards)
            current_display_number = self.current_card_index + 1
            self.ui.labelCardCount.setText(f"{current_display_number}/{total_cards}")

    def update_navigation_buttons_state(self):
        """Cập nhật trạng thái bật/tắt của các nút điều hướng."""
        self.ui.pushButtonPrevious.setEnabled(self.current_card_index > 0)
        self.ui.pushButtonNext.setEnabled(self.current_card_index < len(self.flashcards) - 1)

    def flip_card_animation(self):
        # Vô hiệu hoá nút Flip để tránh bấm liên tục
        self.ui.pushButtonFlip.setEnabled(False)

        # Dừng animation cũ nếu chúng đang chạy
        if self.anim1 and self.anim1.state() == QAbstractAnimation.State.Running:
            self.anim1.stop()
        if self.anim2 and self.anim2.state() == QAbstractAnimation.State.Running:
            self.anim2.stop()

        self.original_card_geometry = self.ui.label_flashcard.geometry()
        center_x = self.original_card_geometry.x() + self.original_card_geometry.width() / 2

        animation_duration = 200

        self.anim1 = QPropertyAnimation(self.ui.label_flashcard, b"geometry")
        self.anim1.setDuration(animation_duration)
        self.anim1.setStartValue(self.original_card_geometry)
        self.anim1.setEndValue(QRect(int(center_x), self.original_card_geometry.y(), 0, self.original_card_geometry.height()))
        self.anim1.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.anim1.finished.connect(self.mid_flip_action)
        self.anim1.start()
    
    def mid_flip_action(self):
        self.is_front_side = not self.is_front_side
        self.show_current_card()

        animation_duration = 200
        center_x_for_anim2_start = self.original_card_geometry.x() + self.original_card_geometry.width() / 2

        self.anim2 = QPropertyAnimation(self.ui.label_flashcard, b"geometry")
        self.anim2.setDuration(animation_duration)
        self.anim2.setStartValue(QRect(int(center_x_for_anim2_start), self.original_card_geometry.y(), 0, self.original_card_geometry.height()))
        self.anim2.setEndValue(self.original_card_geometry)
        self.anim2.setEasingCurve(QEasingCurve.Type.InQuad)

        # ✅ Bật lại nút flip sau khi animation hoàn tất
        self.anim2.finished.connect(lambda: self.ui.pushButtonFlip.setEnabled(True))

        self.anim2.start()
            

    def evaluate_card(self, status):
        if not self.flashcards:
            return

        current_card = self.flashcards[self.current_card_index]
        current_card.status = status
        
        all_user_flashcards = self.db.lay_flashcards_cua_nguoi_dung(self.user_id)
        i_index = 0
        while i_index < len(all_user_flashcards):
            card = all_user_flashcards[i_index]
            if card.id == current_card.id:
                all_user_flashcards[i_index] = current_card
                break
            i_index += 1
        self.db.cap_nhat_flashcards_cho_nguoi_dung(self.user_id, all_user_flashcards)

        self.show_next_card()

    def speak_current_text(self):
        if not self.flashcards:
            QMessageBox.warning(self
, "Lỗi", "Không có văn bản để phát âm.")
            return
        
        current_card = self.flashcards[self.current_card_index]
        text_to_speak = current_card.front_text if self.is_front_side else current_card.back_text

        if not text_to_speak.strip():
            QMessageBox.information(self, "Thông báo", "Văn bản trống, không thể phát âm.")
            return

        if self.speak_thread and self.speak_thread.isRunning():
            self.speak_thread.stop_speaking()
            self.speak_thread.wait()

        self.speak_thread = SpeakService(text_to_speak)
        self.speak_thread.error.connect(lambda msg: QMessageBox.warning(self, "Lỗi Phát Âm", msg))
        self.speak_thread.finished.connect(lambda: None)
        self.speak_thread.start()

    def show_previous_card(self):
        if self.current_card_index > 0:
            self.current_card_index -= 1
            self.is_front_side = True
            self.show_current_card()

    def show_next_card(self):
        if self.current_card_index < len(self.flashcards) - 1:
            self.current_card_index += 1
            self.is_front_side = True
            self.show_current_card()
        else:
            QMessageBox.information(self, "Hoàn thành", "Bạn đã hoàn thành phiên học!")
            self.close()

    def closeEvent(self, event):
        if self.speak_thread and self.speak_thread.isRunning():
            self.speak_thread.stop_speaking()
            self.speak_thread.wait()
        super().closeEvent(event)

class Nhap(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.resize(600, 600)
        self.move(90, 100)
        uic.loadUi("ui/poppup2.ui", self)
        self.can_ve: QLabel = self.findChild(QLabel, "drawing_canvas")
        self.can_ve.setMouseTracking(True)
        self.can_ve.setCursor(Qt.CursorShape.CrossCursor)
        self.buc_ve = QPixmap()
        self.dang_ve = False
        self.diem_cuoi = QPoint()
        self.mau_but = QColor(Qt.GlobalColor.black)
        self.do_day_but = 2

        self.can_ve.mousePressEvent = self.su_kien_nhan_chuot
        self.can_ve.mouseMoveEvent = self.su_kien_di_chuyen_chuot
        self.can_ve.mouseReleaseEvent = self.su_kien_tha_chuot

        self.clear_button.clicked.connect(self.xoa_man_hinh)
        self.color_button.clicked.connect(self.chon_mau_but)
        self.close_button.clicked.connect(self.accept)
        self.load_image_button.clicked.connect(self.tai_anh)

        self.anh_goc = None

    def tai_anh(self):
        hop_thoai = QFileDialog(self)
        hop_thoai.setNameFilter("Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if hop_thoai.exec():
            ten_tap_tin = hop_thoai.selectedFiles()[0]
            pixmap = QPixmap(ten_tap_tin)
            if not pixmap.isNull():
                self.buc_ve = pixmap.scaled(
                    self.can_ve.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.can_ve.setPixmap(self.buc_ve)
                self.anh_goc = self.buc_ve.copy()

    def su_kien_nhan_chuot(self, su_kien):
        if su_kien.button() == Qt.MouseButton.LeftButton:
            self.dang_ve = True
            self.diem_cuoi = su_kien.pos()

    def su_kien_di_chuyen_chuot(self, su_kien):
        if self.dang_ve and su_kien.buttons() == Qt.MouseButton.LeftButton:
            if self.buc_ve.isNull():
                self.buc_ve = QPixmap(self.can_ve.size())
                self.buc_ve.fill(Qt.GlobalColor.white)
                self.can_ve.setPixmap(self.buc_ve)
                return

            hoa_si = QPainter(self.buc_ve)
            do_day = max(1, self.do_day_but)
            but = QPen(self.mau_but, do_day, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            hoa_si.setPen(but)
            hoa_si.drawLine(self.diem_cuoi, su_kien.pos())
            self.diem_cuoi = su_kien.pos()
            hoa_si.end()
            self.can_ve.setPixmap(self.buc_ve)

    def su_kien_tha_chuot(self, su_kien):
        if su_kien.button() == Qt.MouseButton.LeftButton:
            self.dang_ve = False

    def xoa_man_hinh(self):
        self.buc_ve.fill(Qt.GlobalColor.white)
        self.can_ve.setPixmap(self.buc_ve)

    def chon_mau_but(self):
        mau = QColorDialog.getColor(self.mau_but, self)
        if mau.isValid():
            self.mau_but = mau

    def lay_anh_ve(self):
        return self.buc_ve

class MayTinh(QDialog):
    def __init__(self):
        super().__init__()
        uic.loadUi("ui/popup.ui", self)
        self.resize(600, 600)
        self.move(9000, 10)
        self.dang_o_ben_phai = False

        self._0.clicked.connect(lambda: self.xu_ly_nut("0"))
        self._1.clicked.connect(lambda: self.xu_ly_nut("1"))
        self._2.clicked.connect(lambda: self.xu_ly_nut("2"))
        self._3.clicked.connect(lambda: self.xu_ly_nut("3"))
        self._4.clicked.connect(lambda: self.xu_ly_nut("4"))
        self._5.clicked.connect(lambda: self.xu_ly_nut("5"))
        self._6.clicked.connect(lambda: self.xu_ly_nut("6"))
        self._7.clicked.connect(lambda: self.xu_ly_nut("7"))
        self._8.clicked.connect(lambda: self.xu_ly_nut("8"))
        self._9.clicked.connect(lambda: self.xu_ly_nut("9"))

        self._plus.clicked.connect(lambda: self.xu_ly_phep_tinh("+"))
        self._minus.clicked.connect(lambda: self.xu_ly_phep_tinh("-"))
        self._mul.clicked.connect(lambda: self.xu_ly_phep_tinh("*"))
        self._div.clicked.connect(lambda: self.xu_ly_phep_tinh("/"))
        self._ce.clicked.connect(lambda: self.xu_ly_nut("CE"))
        self._equal.clicked.connect(lambda: self.xu_ly_nut("="))

    def xu_ly_nut(self, nut):
        if nut == "CE":
            self.num1.setText("")
            self.num2.setText("")
            self.math.setText("")
            self.dang_o_ben_phai = False
            return

        if nut == "=":
            so1 = self.num1.text()
            so2 = self.num2.text()
            phep_toan = self.math.text()

            if not so1 or not so2:
                return

            try:
                so1 = float(so1)
                so2 = float(so2)
            except ValueError:
                return

            if phep_toan == "+":
                ket_qua = so1 + so2
            elif phep_toan == "-":
                ket_qua = so1 - so2
            elif phep_toan == "*":
                ket_qua = so1 * so2
            elif phep_toan == "/":
                if so2 == 0:
                    ket_qua = "Lỗi"
                else:
                    ket_qua = so1 / so2
            else:
                return

            self.txtDisplay.setText(str(ket_qua))
            return

        if self.dang_o_ben_phai:
            self.num2.setText(self.num2.text() + nut)
        else:
            self.num1.setText(self.num1.text() + nut)

    def xu_ly_phep_tinh(self, phep_toan):
        self.dang_o_ben_phai = True
        self.math.setText(phep_toan)

class TrangChu(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = uic.loadUi("ui/untitled.ui", self)
        self.hop_thong_bao = QMessageBox()
        self.selected_profile_image_path_temp = None
        self.current_user_data = None
        self.db = None
        self.user_id = None
        self.dialogs = [] 
        # Timer chính cho đếm ngược
        self.bo_dem = QTimer(self)
        self.bo_dem.timeout.connect(self.cap_nhat_bo_dem)
        self.thiet_lap_thoi_gian(0)

        # Timer dịch văn bản
        self.bo_dem_dich = QTimer(self)
        self.bo_dem_dich.setInterval(500)
        self.bo_dem_dich.setSingleShot(True)
        self.bo_dem_dich.timeout.connect(self.dich_van_ban)

        # QWebEngineView cho trình duyệt mini
        self.webview = QWebEngineView()
        browser_layout = QVBoxLayout(self.ui.browser_frame)
        self.ui.browser_frame.setLayout(browser_layout)
        browser_layout.addWidget(self.webview)

        # Dịch ngôn ngữ
        self.dich_gia = GoogleTranslator()
        self.nap_ngon_ngu()

        # Gán sự kiện các nút
        self._khoi_tao_signal()

        # Thiết lập combobox phương pháp học
        self.ui.comboBoxStudy.addItems(PHUONG_PHAP_HOC.keys())
        self.ui.comboBoxStudy.currentIndexChanged.connect(self.hien_thi_huong_dan)
        self.cap_nhat_combo_thoi_gian()

    def _khoi_tao_signal(self):
        # Đếm ngược
        self.ui.startButton.clicked.connect(self.bat_dau_dem_nguoc)
        self.ui.pauseButton.clicked.connect(self.dung_dem_nguoc)
        self.ui.pushReset.clicked.connect(self.thiet_lap_lai)
        self.ui.btnApplyTime.clicked.connect(self.ap_dung_thoi_gian_hoc)

        # Nhạc
        self.ui.startButton_2.clicked.connect(self.dung_nhac)
        self.ui.startButton_3.clicked.connect(self.bat_nhac)
        
        # đếm xuôi 
        self.ui.startButton_up.clicked.connect(self.dem_xuoi)
        self.ui.pauseButton_up.clicked.connect(self.dung_dem_xuoi)
        self.bo_dem_xuoi = QTimer(self)
        self.so_giay = 0
        self.bo_dem_xuoi.timeout.connect(self.cap_nhat_hien_thi_xuoi)

        # Điều hướng trang
        self.ui.btnNavHome.clicked.connect(lambda: self.chuyen_trang(0))
        self.ui.btnNavSr.clicked.connect(lambda: self.chuyen_trang(1))
        self.ui.btnNavSetting.clicked.connect(lambda: self.chuyen_trang(2))
        self.ui.btnNavHow.clicked.connect(lambda: self.chuyen_trang(3))

        # Đăng xuất
        self.ui.btnLogout.clicked.connect(self.dang_xuat)

        # Flashcard
        self.ui.BTNopenflashcard.clicked.connect(self.openflashcard)

        # Cửa sổ pop-up
        self.ui.btnOpenPopup.clicked.connect(self.mo_may_tinh)
        self.ui.btnOpenPopup_2.clicked.connect(self.mo_nhap)
        self.ui.btnOpenPopup_3.clicked.connect(self.mo_sub)

        # Tìm nhạc / Google
        self.ui.btnSR.clicked.connect(self.tim_nhac)
        self.ui.btnSR_2.clicked.connect(self.thuc_hien_tim_kiem_google_trong_ung_dung)

        # Thêm phương pháp học
        self.ui.addwaystudy.clicked.connect(self.hien_thi_dialog_them_phuong_phap)

        # Cập nhật info
        self.ui.btnUpdateInfo.clicked.connect(self.cap_nhat_thong_tin_nguoi_dung_tong_hop)
        self.ui.pushButton.clicked.connect(self.tai_anh_dai_dien)

        # Dịch ngôn ngữ (text + thay đổi ngôn ngữ)
        self.ui.input_text_3.textChanged.connect(self.van_ban_thay_doi)
        self.ui.dest_lang_combo.currentIndexChanged.connect(self.ngon_ngu_thay_doi)
        self.ui.src_lang_combo.currentIndexChanged.connect(self.ngon_ngu_thay_doi)
    
    def thiet_lap_thoi_gian_xuoi(self, tong_giay=0):
        self.so_giay = tong_giay
        self.cap_nhat_hien_thi_xuoi()

    def dem_xuoi(self):
        if not self.bo_dem_xuoi.isActive():
            self.bo_dem_xuoi.start(1000)
            self.cap_nhat_hien_thi_xuoi()
        try:
            pygame.mixer.init()
            if not pygame.mixer.music.get_busy():
                pygame.mixer.music.load("music/1 Hour Lofi Vibes - Deep Focus & Relax - No Lyrics.mp3")
                pygame.mixer.music.play(-1)
        except Exception as e:
            print("Lỗi phát nhạc:", e)

    def cap_nhat_hien_thi_xuoi(self):
        self.so_giay += 1
        gio = self.so_giay // 3600
        phut = (self.so_giay // 60) % 60
        giay = self.so_giay % 60
        self.ui.h.setText(f"{gio:02d}")
        self.ui.m.setText(f"{phut:02d}")
        self.ui.s.setText(f"{giay:02d}")

    def dung_dem_xuoi(self):
        self.bo_dem_xuoi.stop()

        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.load("music/chuong.mp3")
            pygame.mixer.music.play()
        except Exception as e:
            print("Lỗi khi dừng nhạc và phát chuông:", e)

        self.hop_thong_bao.information(
            self,
            "Thông báo",
            "⏰ Hết giờ rồi! Nghỉ ngơi một chút nha ~"
        )

    def openflashcard(self):
        if self.current_user_data and self.db:
            self.flashcard_main_window = FlashcardQuanLy(self.current_user_data["id"], self.db, self)
            self.flashcard_main_window.show()
        else:
            self.hop_thong_bao.warning(self, "Lỗi", "Không có người dùng hiện tại hoặc cơ sở dữ liệu chưa sẵn sàng.")

    def mo_may_tinh(self):
        self.may_tinh = MayTinh()
        self.may_tinh.setWindowFlag(Qt.WindowType.Window)
        self.may_tinh.show()

    def tim_nhac(self):
        truy_van = self.ui.SR.text().strip()  # Xóa khoảng trắng dư thừa
        if truy_van:
            # Mã hóa truy vấn để xử lý các ký tự đặc biệt và khoảng trắng
            # Sử dụng urllib.parse.quote_plus để mã hóa đúng cách cho URL truy vấn
            from urllib.parse import quote_plus
            truy_van_ma_hoa = quote_plus(truy_van)

            # Tạo URL tìm kiếm YouTube chính xác
            url = f"https://music.youtube.com/search?q={truy_van_ma_hoa}"
            webbrowser.open(url)
            print(f"Đã mở URL: {url}") # Để kiểm tra URL được tạo
    
    def thuc_hien_tim_kiem_google_trong_ung_dung(self):
        truy_van = self.ui.txtSRR.text().strip()
        if truy_van:
            truy_van_mahoa = urllib.parse.quote(truy_van)
            url = QUrl(f"https://www.google.com/search?q={truy_van_mahoa}")
            self.webview.setUrl(url)
        else:
            self.hop_thong_bao.warning(self, "Cảnh báo", "Vui lòng nhập truy vấn để tìm kiếm Google.")

    def mo_sub(self):
        self.nhap = SubtitleDialog()
        self.nhap.setWindowFlag(Qt.WindowType.Window)
        self.nhap.show()

    def mo_nhap(self):
        self.nhap = Nhap()
        self.nhap.setWindowFlag(Qt.WindowType.Window)
        self.nhap.show()

    def keyPressEvent(self, su_kien):
        if su_kien.key() == Qt.Key.Key_Escape:
            # Đóng tất cả dialog khi nhấn ESC
            for dlg in self.dialogs:
                dlg.close()
            self.close()
            su_kien.accept()

        elif su_kien.key() == Qt.Key.Key_Return:
            if self.ui.txtSRR.hasFocus():
                self.thuc_hien_tim_kiem_google_trong_ung_dung()
            else:
                self.tim_nhac()

    def nap_ngon_ngu(self):
        self.dest_lang_combo.clear()
        self.src_lang_combo.clear()

        self.dest_lang_combo.addItem("Tự động phát hiện", "auto")

        ngon_ngu_da_sap_xep = sorted(NGON_NGU_DICH_THUAT.items(), key=lambda item: item[1])

        for ma, ten in ngon_ngu_da_sap_xep:
            ten_hien_thi = f"{ten.capitalize()} ({ma})"
            self.dest_lang_combo.addItem(ten_hien_thi, ma)
            self.src_lang_combo.addItem(ten_hien_thi, ma)

        vi_tri_tieng_anh = self.src_lang_combo.findData("en")
        if vi_tri_tieng_anh != -1:
            self.src_lang_combo.setCurrentIndex(vi_tri_tieng_anh)

    def van_ban_thay_doi(self):
        self.bo_dem_dich.stop()
        self.bo_dem_dich.start()

    def dich_van_ban(self):
        van_ban = self.input_text_3.toPlainText().strip()
        if not van_ban:
            self.output_text_3.clear()
            return

        nguon = self.dest_lang_combo.currentData()
        dich = self.src_lang_combo.currentData()

        if self.bo_dem_dich.isActive():
            self.bo_dem_dich.stop()

        van_ban_da_dich = self.dich_gia.translate(
            text=van_ban,
            target=dich,
            source=nguon if nguon != 'auto' else None
        )
        self.output_text_3.setPlainText(van_ban_da_dich)

    def ngon_ngu_thay_doi(self):
        self.dich_van_ban()

    def kiem_tra_email(self, email): # Thêm hàm kiểm tra email vào TrangChu
        bieu_thuc = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        return re.match(bieu_thuc, email) is not None

    def hien_thi_thong_tin_nguoi_dung(self):
        if self.current_user_data:
            self.ui.lineEdit.setText(self.current_user_data.get("username", ""))
            self.ui.lineEdit_3.setText(self.current_user_data.get("email", ""))
            self.ui.lineEdit_2.setText(self.current_user_data.get("dob", ""))
            self.ui.lineEdit_4.setText(self.current_user_data.get("phone", ""))
            # Không hiển thị mật khẩu ở đây vì lý do bảo mật
            self.ui.lineEdit_5.clear() # Xóa trường mật khẩu mỗi khi hiển thị thông tin
        else:
            self.hop_thong_bao.warning(self, "Lỗi", "Không tìm thấy thông tin người dùng.")
    
    def hien_thi_anh_dai_dien(self):
        # Giả định label hiển thị ảnh có objectName là 'label_profile_pic'
        if self.current_user_data and self.current_user_data.get("profile_picture_path"):
            avatar_path = self.current_user_data.get("profile_picture_path")
            full_path = os.path.join("data", "avatars", avatar_path) # Đường dẫn đầy đủ

            if os.path.exists(full_path):
                pixmap = QPixmap(full_path)
                if not pixmap.isNull():
                    # Nếu scaledContents đã được đặt trong Qt Designer, không cần scale thủ công ở đây
                    self.ui.label_profile_pic.setPixmap(pixmap)
                    self.ui.label_profile_pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    self.ui.label_profile_pic.clear()
                    self.hop_thong_bao.warning(self, "Lỗi ảnh", "Không thể tải ảnh đại diện.")
            else:
                self.ui.label_profile_pic.clear()
                # Load default image if the specified path does not exist
                default_pixmap = QPixmap("image/ảnh.png") # Assuming default_avatar.jpg is your default image
                if not default_pixmap.isNull():
                    self.ui.label_profile_pic.setPixmap(default_pixmap)
                    self.ui.label_profile_pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    self.ui.label_profile_pic.setText("Không có ảnh") # Fallback text if default image also fails
        else:
            self.ui.label_profile_pic.clear()
            # Load default image if no profile_picture_path is set
            default_pixmap = QPixmap("image/ảnh.png") # Assuming default_avatar.jpg is your default image
            if not default_pixmap.isNull():
                self.ui.label_profile_pic.setPixmap(default_pixmap)
                self.ui.label_profile_pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                self.ui.label_profile_pic.setText("Chưa có ảnh đại diện") # Fallback text if default image also fails

    def tai_anh_dai_dien(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh đại diện",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.gif)"
        )

        if file_path:
            # Lưu đường dẫn tạm thời của ảnh gốc
            self.selected_profile_image_path_temp = file_path

            # Hiển thị ảnh lên QLabel ngay lập tức
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self.ui.label_profile_pic.setPixmap(pixmap)
                self.ui.label_profile_pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                self.ui.label_profile_pic.clear()
                self.hop_thong_bao.warning(self, "Lỗi ảnh", "Không thể tải ảnh đại diện để xem trước.")
        else:
            self.hop_thong_bao.information(self, "Thông báo", "Bạn chưa chọn ảnh nào.")

    def cap_nhat_thong_tin_nguoi_dung_tong_hop(self): # Hàm mới để cập nhật tổng hợp
        if not self.current_user_data or not self.db:
            self.hop_thong_bao.warning(self, "Lỗi", "Không có dữ liệu người dùng để cập nhật.")
            return

        # Khởi tạo dữ liệu cập nhật với thông tin hiện tại
        updated_data = self.current_user_data.copy()

        # Lấy dữ liệu từ LineEdit
        new_username = self.ui.lineEdit.text().strip()
        new_email = self.ui.lineEdit_3.text().strip()
        new_dob = self.ui.lineEdit_2.text().strip()
        new_phone = self.ui.lineEdit_4.text().strip()
        new_password = self.ui.lineEdit_5.text().strip()

        # Cập nhật các trường - chỉ cập nhật nếu người dùng nhập dữ liệu mới
        # Nếu người dùng xóa sạch ô input, nó sẽ giữ lại giá trị cũ nếu có
        if new_username:
            updated_data["username"] = new_username
        elif "username" not in updated_data: # Đảm bảo key tồn tại nếu không có giá trị cũ
            updated_data["username"] = ""

        if new_email:
            if not self.kiem_tra_email(new_email):
                self.hop_thong_bao.warning(self, "Lỗi", "Email không hợp lệ.")
                return
            updated_data["email"] = new_email
        elif "email" not in updated_data:
            updated_data["email"] = ""

        if new_dob:
            updated_data["dob"] = new_dob
        elif "dob" not in updated_data:
            updated_data["dob"] = ""

        if new_phone:
            updated_data["phone"] = new_phone
        elif "phone" not in updated_data:
            updated_data["phone"] = ""

        if new_password:
            updated_data["password"] = new_password
        elif "password" not in updated_data:
            updated_data["password"] = ""

        # Xử lý ảnh đại diện nếu có ảnh mới được chọn
        if self.selected_profile_image_path_temp:
            avatar_dir = os.path.join("data", "avatars")
            if not os.path.exists(avatar_dir):
                os.makedirs(avatar_dir)

            file_name = os.path.basename(self.selected_profile_image_path_temp)
            # Tạo tên file duy nhất bằng cách kết hợp username và timestamp
            unique_file_name = f"{updated_data.get('username', 'user')}_{os.path.getmtime(self.selected_profile_image_path_temp)}_{file_name}"
            destination_path = os.path.join(avatar_dir, unique_file_name)

            try:
                shutil.copy(self.selected_profile_image_path_temp, destination_path)
                updated_data["profile_picture_path"] = unique_file_name
                self.selected_profile_image_path_temp = None # Reset sau khi lưu
            except Exception as e:
                self.hop_thong_bao.critical(self, "Lỗi sao chép", f"Không thể sao chép ảnh: {e}")
                return # Dừng cập nhật nếu sao chép ảnh lỗi

        # Cập nhật trong cơ sở dữ liệu
        # Sử dụng username cũ để tìm kiếm bản ghi để cập nhật
        old_username = self.current_user_data.get("username")
        if self.db.cap_nhat_du_lieu(old_username, updated_data):
            self.current_user_data = updated_data # Cập nhật dữ liệu người dùng hiện tại
            self.hop_thong_bao.information(self, "Thành công", "Thông tin đã được cập nhật.")
            self.hien_thi_anh_dai_dien() # Hiển thị ảnh đại diện mới
            self.hien_thi_thong_tin_nguoi_dung() # Cập nhật hiển thị các trường thông tin
        else:
            self.hop_thong_bao.warning(self, "Lỗi", "Không thể cập nhật thông tin người dùng.")

    def dang_xuat(self):
        self.close()
        man_hinh_dang_nhap.show()

    def chuyen_trang(self, chi_muc):
        self.ui.stackedWidget.setCurrentIndex(chi_muc)

    def thiet_lap_lai(self):
        self.thiet_lap_thoi_gian(0)

    def set_current_user(self, user_data, db_instance):
        self.current_user_data = user_data
        self.db = db_instance
        self.user_id = user_data.get("id")
        
        # Gọi tất cả các hàm cần thiết sau khi thiết lập user
        self.hien_thi_thong_tin_nguoi_dung()
        self.hien_thi_anh_dai_dien()
        self.tai_va_hien_thi_phuong_phap_hoc()

    def tai_va_hien_thi_phuong_phap_hoc(self):
        self.ui.comboBoxStudy.clear()
        self.ui.comboBoxTime.clear()
        
        # Ngắt kết nối tín hiệu tạm thời để tránh kích hoạt sự kiện khi xóa/thêm mục
        try:
            self.ui.comboBoxStudy.currentIndexChanged.disconnect(self.hien_thi_huong_dan)
        except TypeError:
            pass # Không làm gì nếu chưa được kết nối

        # Thêm các phương pháp mặc định
        for phuong_phap, chi_tiet in PHUONG_PHAP_HOC.items():
            self.ui.comboBoxStudy.addItem(phuong_phap)
            # THOI_GIAN_HOC có thể chỉ chứa tên phương pháp, không có chi tiết mô tả như PHUONG_PHAP_HOC
            # Nên việc lấy thời gian cần phải từ THOI_GIAN_HOC
            self.ui.comboBoxTime.addItem(f"{phuong_phap} - {THOI_GIAN_HOC.get(phuong_phap, 0) // 60} phút")

        # Thêm các phương pháp tùy chỉnh của người dùng
        if self.current_user_data and self.db and self.user_id:
            user_methods = self.db.lay_phuong_phap_cua_nguoi_dung(self.user_id)
            for method in user_methods:
                self.ui.comboBoxStudy.addItem(method["name"])
                self.ui.comboBoxTime.addItem(f"{method['name']} - {method['recommended_time'] // 60} phút")
        
        # Kết nối lại tín hiệu sau khi đã cập nhật xong
        self.ui.comboBoxStudy.currentIndexChanged.connect(self.hien_thi_huong_dan)

    def ap_dung_thoi_gian_hoc(self):
        van_ban_chon = self.ui.comboBoxTime.currentText()
        if not van_ban_chon:
            self.hop_thong_bao.warning(self, "Lỗi", "Vui lòng chọn một phương pháp học để áp dụng thời gian.")
            return

        ten_phuong_phap = van_ban_chon.split(" - ")[0].strip()
        
        thoi_gian_giay = 0
        
        # Kiểm tra trong phương pháp mặc định
        if ten_phuong_phap in THOI_GIAN_HOC:
            thoi_gian_giay = THOI_GIAN_HOC[ten_phuong_phap]
        elif self.current_user_data and self.db and self.user_id: # Kiểm tra trong phương pháp của người dùng
            user_methods = self.db.lay_phuong_phap_cua_nguoi_dung(self.user_id)
            for method in user_methods:
                if method["name"] == ten_phuong_phap:
                    thoi_gian_giay = method["recommended_time"]
                    break

        if thoi_gian_giay > 0:
            self.thiet_lap_thoi_gian(thoi_gian_giay)
            self.hop_thong_bao.information(
                self,
                "Thời gian đã đặt",
                f"Đã đặt thời gian {thoi_gian_giay // 60} phút."
            )
        else:
            self.hop_thong_bao.warning(
                self,
                "Lỗi",
                "Không tìm thấy thời gian cho phương pháp này."
            )

    def thiet_lap_thoi_gian(self, tong_giay):
        self.thoi_gian_con_lai = tong_giay
        self.cap_nhat_hien_thi()

    def cap_nhat_hien_thi(self):
        gio = self.thoi_gian_con_lai // 3600
        phut = (self.thoi_gian_con_lai % 3600) // 60
        giay = self.thoi_gian_con_lai % 60
        self.ui.h.setText(f"{gio:02d}")
        self.ui.m.setText(f"{phut:02d}")
        self.ui.s.setText(f"{giay:02d}")

    def bat_dau_dem_nguoc(self):
        if self.thoi_gian_con_lai <= 0:
            self.hop_thong_bao.warning(self, "Lỗi", "Vui lòng chọn hoặc đặt thời gian trước khi bắt đầu.")
            return

        if not self.bo_dem.isActive():
            self.bo_dem.start(1000)
        pygame.mixer.init()
        pygame.mixer.music.load("music/1 Hour Lofi Vibes - Deep Focus & Relax - No Lyrics.mp3")
        pygame.mixer.music.play(-1)

    def cap_nhat_bo_dem(self):
        if self.thoi_gian_con_lai > 0:
            self.thoi_gian_con_lai -= 1
            self.cap_nhat_hien_thi()
        else:
            self.bo_dem.stop()
            pygame.mixer.music.stop()
            pygame.mixer.init()
            pygame.mixer.music.load("music/chuong.mp3")
            pygame.mixer.music.play()
            self.hop_thong_bao.information(
                self,
                "Thông báo",
                "⏰ Hết giờ rồi! Nghỉ ngơi một chút nha ~"
            )

    def dung_nhac(self):
        pygame.mixer.init()
        pygame.mixer.music.stop()

    def bat_nhac(self):
        pygame.mixer.init()
        pygame.mixer.music.load("music/1 Hour Lofi Vibes - Deep Focus & Relax - No Lyrics.mp3")
        pygame.mixer.music.play(-1)

    def dung_dem_nguoc(self):
        self.bo_dem.stop()
        pygame.mixer.init()
        pygame.mixer.music.stop()

    def hien_thi_huong_dan(self):
        phuong_phap_chon = self.ui.comboBoxStudy.currentText()
        if not phuong_phap_chon:
            return

        huong_dan = ""
        # Kiểm tra trong phương pháp mặc định
        if phuong_phap_chon in PHUONG_PHAP_HOC:
            chi_tiet = PHUONG_PHAP_HOC[phuong_phap_chon]
            huong_dan = "\n".join(chi_tiet)
        elif self.current_user_data and self.db and self.user_id: # Kiểm tra trong phương pháp của người dùng
            user_methods = self.db.lay_phuong_phap_cua_nguoi_dung(self.user_id)
            for method in user_methods:
                if method["name"] == phuong_phap_chon:
                    huong_dan = method["description"]
                    break

        if huong_dan:
            self.hop_thong_bao.information(
                self,
                "Cách thực hiện",
                f"Phương pháp: {phuong_phap_chon}\n\n{huong_dan}"
            )
        else:
            self.hop_thong_bao.warning(
                self,
                "Lỗi",
                "Không tìm thấy hướng dẫn cho phương pháp này."
            )

    def hien_thi_dialog_them_phuong_phap(self):
        if not self.current_user_data or not self.user_id:
            self.hop_thong_bao.warning(self, "Lỗi", "Vui lòng đăng nhập để thêm phương pháp học.")
            return

        # Tải UI của dialog từ file .ui
        self.dialog = QDialog(self)
        uic.loadUi("ZenTask/add_method_dialog.ui", self.dialog)

        # Liên kết các widget từ UI dialog
        self.ten_phuong_phap_input = self.dialog.lineEdit_tenPhuongPhap
        self.mo_ta_input = self.dialog.textEdit_moTa
        self.thoi_gian_input = self.dialog.lineEdit_thoiGian
        self.btn_them = self.dialog.pushButton_them
        self.btn_huy = self.dialog.pushButton_huy


        # Thiết lập placeholder text (tùy chọn)
        if self.ten_phuong_phap_input:
            self.ten_phuong_phap_input.setPlaceholderText("Ví dụ: Kỹ thuật ghi nhớ")
        if self.mo_ta_input:
            self.mo_ta_input.setPlaceholderText("Giải thích cách thực hiện phương pháp này...")
        if self.thoi_gian_input:
            self.thoi_gian_input.setPlaceholderText("Ví dụ: 30")
        
        # Liên kết các tín hiệu
        if self.btn_them:
            self.btn_them.clicked.connect(lambda: self.them_phuong_phap_moi(
                self.ten_phuong_phap_input.text(),
                self.mo_ta_input.toPlainText(),
                self.thoi_gian_input.text(),
                self.dialog
            ))
        if self.btn_huy:
            self.btn_huy.clicked.connect(self.dialog.reject) # Đóng dialog với kết quả Reject

        self.dialog.exec()

    def them_phuong_phap_moi(self, ten_phuong_phap, mo_ta, thoi_gian_phut_str, dialog):
        if not ten_phuong_phap.strip() or not mo_ta.strip() or not thoi_gian_phut_str.strip():
            self.hop_thong_bao.warning(self, "Lỗi", "Vui lòng điền đầy đủ thông tin.")
            return

        try:
            thoi_gian_phut = int(thoi_gian_phut_str)
            if thoi_gian_phut <= 0:
                raise ValueError
        except ValueError:
            self.hop_thong_bao.warning(self, "Lỗi", "Thời gian khuyến nghị phải là một số nguyên dương.")
            return

        thoi_gian_giay = thoi_gian_phut * 60

        if self.db.them_phuong_phap_cho_nguoi_dung(self.user_id, ten_phuong_phap.strip(), mo_ta.strip(), thoi_gian_giay):
            self.hop_thong_bao.information(self, "Thành công", f"Đã thêm phương pháp '{ten_phuong_phap}'.")
            self.tai_va_hien_thi_phuong_phap_hoc() # Cập nhật lại danh sách phương pháp trên ComboBox
            dialog.accept() # Đóng dialog
        else:
            self.hop_thong_bao.warning(self, "Lỗi", f"Phương pháp '{ten_phuong_phap}' đã tồn tại hoặc có lỗi xảy ra khi lưu.")
    
    def cap_nhat_combo_thoi_gian(self):
        self.ui.comboBoxTime.clear()
        for phuong_phap, giay in THOI_GIAN_HOC.items():
            phut = giay // 60
            self.ui.comboBoxTime.addItem(f"{phuong_phap} - {phut} phút")

class DangNhap(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = uic.loadUi("ui/login.ui", self)
        self.hop_thong_bao = QMessageBox()
        self.ui.btnLogin.clicked.connect(self.xu_ly_dang_nhap)
        self.ui.btnLo.clicked.connect(self.chuyen_dang_ky)
        self.showFullScreen()

        self.nhan_tieu_de: QLabel = self.findChild(QLabel, "labelTitle")
        self.nhan_tieu_de.setText("")
        self.nhan_tieu_de.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.co_so_du_lieu = CoSoDuLieuNguoiDung()
        self.co_so_du_lieu.tai_du_lieu()
        self.thiet_lap_kieu_chu()
        self.thiet_lap_may_danh_chu()

    def chuyen_dang_ky(self):
        self.close()
        man_hinh_dang_ky.showFullScreen()

    def xu_ly_dang_nhap(self):
        ten = self.ui.txtEmail_2.text()
        email = self.ui.txtEmail.text()
        mat_khau = self.ui.txtPassword.text()
        nut_chon = self.ui.checkBox.checkState()

        if not email.strip() or not mat_khau.strip() or not ten.strip():
            self.hop_thong_bao.setText("Tên, Email hoặc mật khẩu không được để trống")
            self.hop_thong_bao.exec()
            return

        if nut_chon != Qt.CheckState.Checked:
            self.hop_thong_bao.setText("Bạn phải đồng ý điều khoản!")
            self.hop_thong_bao.exec()
            return

        if email.strip() == "admin" and mat_khau.strip() == "123":
            self.hop_thong_bao.setText("Đăng nhập thành công!")
            self.hop_thong_bao.exec()
            # Đối với admin, có thể không cần user_data cụ thể, hoặc tạo một user_data admin giả
            man_hinh_trang_chu.showMaximized()
            self.close()
            return

        if not self.kiem_tra_email(email):
            self.hop_thong_bao.setText("Email không hợp lệ. Vui lòng nhập lại.")
            self.hop_thong_bao.exec()
            return

        user_data = self.co_so_du_lieu.xac_thuc_dang_nhap(email, mat_khau, ten)
        if user_data:
            self.hop_thong_bao.setText("Đăng nhập thành công!")
            self.hop_thong_bao.exec()
            man_hinh_trang_chu.set_current_user(user_data, self.co_so_du_lieu)
            man_hinh_trang_chu.showMaximized()
            self.close()
        else:
            self.hop_thong_bao.setText("Tên, Email hoặc mật khẩu không đúng.")
            self.hop_thong_bao.exec()

    def kiem_tra_email(self, email):
        bieu_thuc = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        result = re.match(bieu_thuc, email) is not None
        return result

    def thiet_lap_kieu_chu(self):
        font = QFont("JetBrains Mono", 24)
        self.nhan_tieu_de.setWordWrap(True)
        self.nhan_tieu_de.setFixedWidth(400)
        font.setBold(True)
        self.nhan_tieu_de.setFont(font)
        self.nhan_tieu_de.setStyleSheet("color: rgba(255, 255, 255, 1);")

    def thiet_lap_may_danh_chu(self):
        self.thiet_lap_kieu_chu()
        self.van_ban_day_du = "Khơi nguồn sức mạnh, tập trung bứt phá giới hạn cùng ZenTask."
        self.vi_tri_hien_tai = 0
        self.bo_dem = QTimer(self)
        self.bo_dem.timeout.connect(self.cap_nhat_van_ban)
        self.bo_dem.start(100)

    def cap_nhat_van_ban(self):
        if self.vi_tri_hien_tai <= len(self.van_ban_day_du):
            van_ban = self.van_ban_day_du[:self.vi_tri_hien_tai]
            self.nhan_tieu_de.setText(van_ban + "|")
            self.vi_tri_hien_tai += 1
        else:
            self.bo_dem.stop()
            self.nhan_tieu_de.setText(self.van_ban_day_du)

    def keyPressEvent(self, su_kien):
        if su_kien.key() == Qt.Key.Key_Escape:
            self.close()
        elif su_kien.key() == Qt.Key.Key_Return:
            self.xu_ly_dang_nhap()

class DangKy(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = uic.loadUi("ui/register.ui", self)
        self.hop_thong_bao = QMessageBox()
        self.ui.btnRegister.clicked.connect(self.xu_ly_dang_ky)
        self.ui.btnLoo.clicked.connect(self.chuyen_dang_nhap)

        self.nhan_tieu_de: QLabel = self.findChild(QLabel, "labelTitle")
        self.nhan_tieu_de.setText("")
        self.nhan_tieu_de.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.thiet_lap_kieu_chu()
        self.thiet_lap_may_danh_chu()
        self.co_so_du_lieu = CoSoDuLieuNguoiDung()
        self.co_so_du_lieu.tai_du_lieu()

    def chuyen_dang_nhap(self):
        self.close()
        man_hinh_dang_nhap.show()

    def xu_ly_dang_ky(self):
        nut_chon = self.ui.checkBox.checkState()
        email = self.ui.txtEmail.text()
        mat_khau = self.ui.txtPassword.text()
        mat_khau_nhap_lai = self.ui.txtPassword_2.text()
        ten = self.ui.txtName.text()

        if mat_khau != mat_khau_nhap_lai:
            self.hop_thong_bao.setText("Mật khẩu không trùng khớp")
            self.hop_thong_bao.exec()
            return

        if not ten.strip():
            self.hop_thong_bao.setText("Tên không được để trống")
            self.hop_thong_bao.exec()
            return

        if not email.strip():
            self.hop_thong_bao.setText("Email không được để trống")
            self.hop_thong_bao.exec()
            return

        if not mat_khau.strip():
            self.hop_thong_bao.setText("Mật khẩu không được để trống")
            self.hop_thong_bao.exec()
            return

        # Kiểm tra xem tên người dùng đã tồn tại chưa (không phân biệt hoa thường)
        if ten.strip().lower() in [user["username"].lower() for user in self.co_so_du_lieu.du_lieu_nguoi_dung]:
            self.hop_thong_bao.setText("Tên đã được sử dụng")
            self.hop_thong_bao.exec()
            return
        
        # Kiểm tra xem email đã tồn tại chưa (không phân biệt hoa thường)
        if email.strip().lower() in [user["email"].lower() for user in self.co_so_du_lieu.du_lieu_nguoi_dung]:
            self.hop_thong_bao.setText("Email đã được sử dụng")
            self.hop_thong_bao.exec()
            return

        if nut_chon != Qt.CheckState.Checked:
            self.hop_thong_bao.setText("Bạn cần đồng ý với điều khoản!")
            self.hop_thong_bao.exec()
            return

        if not self.kiem_tra_email(email):
            self.hop_thong_bao.setText("Email không hợp lệ. Vui lòng nhập lại.")
            self.hop_thong_bao.exec()
            return

        new_user_data = {
            "username": ten.strip(),
            "password": mat_khau.strip(),
            "email": email.strip(),
            "flashcards": [],
            "study_methods": [] # Khởi tạo danh sách phương pháp học rỗng cho người dùng mới
        }
        self.co_so_du_lieu.luu_du_lieu(new_user_data) # Lưu dữ liệu với trường study_methods

        self.hop_thong_bao.setText(
            f"Chào mừng {ten} đến với Focused Time, "
            "hy vọng ứng dụng có thể giúp bạn học tập hiệu quả hơn 💕"
        )
        self.hop_thong_bao.exec()
        man_hinh_trang_chu.set_current_user(new_user_data, self.co_so_du_lieu)
        man_hinh_trang_chu.showMaximized()
        self.close()

    def kiem_tra_email(self, email):
        bieu_thuc = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        result = re.match(bieu_thuc, email) is not None
        return result

    def thiet_lap_kieu_chu(self):
        font = QFont("JetBrains Mono", 24)
        self.nhan_tieu_de.setWordWrap(True)
        self.nhan_tieu_de.setFixedWidth(400)
        font.setBold(True)
        self.nhan_tieu_de.setFont(font)
        self.nhan_tieu_de.setStyleSheet("color: rgba(255, 255, 255, 1);")

    def thiet_lap_may_danh_chu(self):
        self.thiet_lap_kieu_chu()
        self.van_ban_day_du = "Khơi nguồn sức mạnh, tập trung bứt phá giới hạn cùng ZenTask."
        self.vi_tri_hien_tai = 0
        self.bo_dem = QTimer(self)
        self.bo_dem.timeout.connect(self.cap_nhat_van_ban)
        self.bo_dem.start(100)

    def cap_nhat_van_ban(self):
        if self.vi_tri_hien_tai <= len(self.van_ban_day_du):
            van_ban = self.van_ban_day_du[:self.vi_tri_hien_tai]
            self.nhan_tieu_de.setText(van_ban + "|")
            self.vi_tri_hien_tai += 1
        else:
            self.bo_dem.stop()
            self.nhan_tieu_de.setText(self.van_ban_day_du)

    def keyPressEvent(self, su_kien):
        if su_kien.key() == Qt.Key.Key_Escape:
            self.close()
        elif su_kien.key() == Qt.Key.Key_Return:
            self.xu_ly_dang_ky()

if __name__ == "__main__":
    if not os.path.exists("data/avatars"):
        os.makedirs("data/avatars")
    ung_dung = QApplication(sys.argv)
    man_hinh_dang_nhap = DangNhap()
    man_hinh_trang_chu = TrangChu()
    man_hinh_dang_ky = DangKy()
    
    man_hinh_dang_nhap.show()
    sys.exit(ung_dung.exec())