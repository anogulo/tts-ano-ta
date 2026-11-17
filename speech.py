"""
speech.py - Sintesis Suara Piper TTS & Pemutaran Audio Streaming
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 5 (FR-08, FR-09), Bagian 13, dan Bagian 18.
Mendukung pemutaran native via aplay (Raspberry Pi/Linux) serta fallback cross-platform.
"""

import os
import platform
import shutil
import subprocess
import threading
import time
import wave
from typing import Callable, Generator, List, Optional

try:
    from piper import PiperVoice
except ImportError:
    PiperVoice = None


class Speaker:
    """
    Kelas pemutar suara Piper TTS dan audio feedback.
    Satu antrean audio serial untuk mencegah konflik perangkat ALSA.
    """

    def __init__(self, model_path: str, device: str = "plughw:1,0"):
        self.model_path = model_path
        self.device = device
        self.voice = None
        self.rate = 22050  # Default Piper sample rate
        self.playing = False
        self._proc: Optional[subprocess.Popen] = None
        self._cue: Optional[subprocess.Popen] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.is_linux = platform.system().lower() == "linux"
        self.has_aplay = shutil.which("aplay") is not None

        self._load_voice()

    def _load_voice(self):
        """Memuat model Piper ONNX ke memori sekali saat inisialisasi."""
        if PiperVoice is None:
            return

        if os.path.exists(self.model_path):
            try:
                self.voice = PiperVoice.load(self.model_path)
                if hasattr(self.voice, 'config') and hasattr(self.voice.config, 'sample_rate'):
                    self.rate = self.voice.config.sample_rate
                print(f"[Speaker] PiperVoice siap (Model: {os.path.basename(self.model_path)}, Rate: {self.rate} Hz).")
            except Exception as e:
                print(f"[Speaker] Peringatan: Gagal memuat PiperVoice: {e}")
                self.voice = None
        else:
            print(f"[Speaker] Peringatan: Berkas model tidak ditemukan di {self.model_path}")
            self.voice = None

    def _pcm(self, text: str) -> Generator[bytes, None, None]:
        """Menghasilkan stream PCM bytes dari sintesis Piper."""
        if self.voice is None:
            return

        if hasattr(self.voice, 'synthesize_stream_raw'):
            # API piper-tts 1.2.x
            yield from self.voice.synthesize_stream_raw(text)
        elif hasattr(self.voice, 'synthesize'):
            # API piper-tts >= 1.3 / 1.8+
            for chunk in self.voice.synthesize(text):
                if hasattr(chunk, 'audio_int16_bytes'):
                    yield chunk.audio_int16_bytes
                elif isinstance(chunk, bytes):
                    yield chunk
                elif hasattr(chunk, 'tobytes'):
                    yield chunk.tobytes()

    def cue(self, path: str, wait: bool = False):
        """
        Memutar berkas audio cue/status (format .wav).
        Menunggu cue sebelumnya selesai agar tidak tumpang tindih.
        """
        with self._lock:
            if self._cue:
                try:
                    self._cue.wait(timeout=3.0)
                except Exception:
                    pass

            if not os.path.exists(path):
                # Abaikan jika berkas cue belum dibuat
                return

            if self.has_aplay:
                self._cue = subprocess.Popen(
                    ['aplay', '-q', '-D', self.device, path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if wait and self._cue:
                    self._cue.wait()
            else:
                # Fallback untuk Windows/macOS/environment tanpa aplay
                self._play_wav_fallback(path, wait=wait)

    def _play_wav_fallback(self, wav_path: str, wait: bool = False):
        """Pemutar wav fallback untuk lingkungan pengujian non-Linux/aplay."""
        try:
            if platform.system() == "Windows":
                import winsound
                flags = winsound.SND_FILENAME
                if not wait:
                    flags |= winsound.SND_ASYNC
                winsound.PlaySound(wav_path, flags)
            else:
                cmd = None
                for candidate in ['paplay', 'afplay', 'ffplay']:
                    if shutil.which(candidate):
                        cmd = [candidate, wav_path]
                        if candidate == 'ffplay':
                            cmd.extend(['-nodisp', '-autoexit'])
                        break
                if cmd:
                    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if wait:
                        p.wait()
        except Exception:
            pass

    def say(self, sentences: List[str], on_first: Optional[Callable[[], None]] = None) -> bool:
        """
        FR-08 & FR-09: Sintesis dan putar per kalimat secara streaming.
        Blok hingga selesai atau dihentikan oleh stop().
        Mengembalikan True jika selesai utuh, False jika diinterupsi / gagal.
        """
        if not sentences:
            return True

        with self._lock:
            if self._cue:
                try:
                    self._cue.wait(timeout=3.0)
                except Exception:
                    pass

        self._stop.clear()
        self.playing = True
        first_audio_called = False

        if self.has_aplay:
            # Jalur utama Raspberry Pi 4 dengan ALSA aplay
            try:
                self._proc = subprocess.Popen(
                    [
                        'aplay', '-q', '-D', self.device,
                        '-t', 'raw', '-f', 'S16_LE',
                        '-c', '1', '-r', str(self.rate), '-'
                    ],
                    stdin=subprocess.PIPE,
                    bufsize=0
                )

                for sentence in sentences:
                    if self._stop.is_set():
                        return False

                    for pcm in self._pcm(sentence):
                        if self._stop.is_set():
                            return False

                        if on_first and not first_audio_called:
                            on_first()
                            first_audio_called = True

                        if self._proc and self._proc.stdin:
                            try:
                                self._proc.stdin.write(pcm)
                            except (BrokenPipeError, OSError):
                                return False

                if self._proc and self._proc.stdin:
                    self._proc.stdin.close()
                    self._proc.wait()
                return not self._stop.is_set()

            except (BrokenPipeError, ValueError, OSError):
                return False
            finally:
                self.playing = False
                if self._proc and self._proc.poll() is None:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None

        else:
            # Fallback jika aplay tidak tersedia (pengujian Windows/CI)
            try:
                for idx, sentence in enumerate(sentences):
                    if self._stop.is_set():
                        return False

                    print(f" [Audio TTS] Sedang membacakan ({idx + 1}/{len(sentences)}): \"{sentence}\"")
                    pcm_chunks = list(self._pcm(sentence))
                    if not pcm_chunks:
                        continue

                    if on_first and not first_audio_called:
                        on_first()
                        first_audio_called = True

                    pcm_data = b''.join(pcm_chunks)
                    # Putar via wave buffer
                    self._play_pcm_buffer(pcm_data)

                    if self._stop.is_set():
                        return False

                return not self._stop.is_set()
            finally:
                self.playing = False

    def _play_pcm_buffer(self, pcm_data: bytes):
        """Memutar buffer PCM sementara pada mode fallback."""
        temp_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "logs",
            f"_temp_speech_{int(time.time() * 1000)}.wav"
        )
        os.makedirs(os.path.dirname(temp_file), exist_ok=True)
        try:
            with wave.open(temp_file, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.rate)
                wf.writeframes(pcm_data)
            self._play_wav_fallback(temp_file, wait=True)
        except Exception as e:
            print(f"[Speaker] Gagal memutar audio: {e}")
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

    def stop(self):
        """FR-09: Menghentikan pemutaran audio seketika saat tombol ditekan."""
        self._stop.set()
        self.playing = False
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
        if self._cue and self._cue.poll() is None:
            try:
                self._cue.kill()
            except Exception:
                pass
            self._cue = None
        if platform.system() == "Windows":
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
