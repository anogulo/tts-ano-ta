"""
speech.py - Sintesis Suara Piper TTS & Pemutaran Audio Streaming
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 5 (FR-08, FR-09), Bagian 13, dan Bagian 18.
Mendukung pemutaran native via aplay (Raspberry Pi/Linux) serta fallback cross-platform.
"""

import os
import platform
import re
import shutil
import subprocess
import threading
import time
import wave
from typing import Callable, Dict, Generator, List, Optional

try:
    from piper import PiperVoice
except ImportError:
    PiperVoice = None


_VOLUME_LOCK = threading.Lock()
_ALSA_CTRL_CACHE: Dict[str, List[str]] = {}


def _resolve_alsa_card(cfg_device: str) -> Optional[str]:
    """Menentukan ID/nama card ALSA dari string device seperti 'plughw:2,0'."""
    if not cfg_device:
        return None
    dev = cfg_device.strip()
    if dev.lower() in ("default", "pulse", "pipewire", "sysdefault"):
        return None
    if ":" in dev:
        part = dev.split(":", 1)[1].split(",", 1)[0].strip()
        if part.upper().startswith("CARD="):
            return part[5:].strip() or None
        if part:
            return part
    return None


def _list_sound_cards() -> List[str]:
    """Membaca daftar indeks card audio dari /proc/asound/cards jika tersedia."""
    cards: List[str] = []
    if os.path.exists("/proc/asound/cards"):
        try:
            with open("/proc/asound/cards", "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = re.match(r"^\s*(\d+)\s+\[", line)
                    if m:
                        cards.append(m.group(1))
        except Exception:
            pass
    return cards or ["0", "1", "2", "3"]


def _get_card_controls(card: str) -> List[str]:
    """Mengambil dan meng-cache daftar simple mixer control pada card ALSA."""
    if card in _ALSA_CTRL_CACHE:
        return _ALSA_CTRL_CACHE[card]
    ctrls: List[str] = []
    try:
        res = subprocess.run(
            ["amixer", "-c", str(card), "scontrols"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            ctrls = re.findall(r"Simple mixer control '([^']+)'", res.stdout)
    except Exception:
        pass
    _ALSA_CTRL_CACHE[card] = ctrls
    return ctrls


def _set_amixer_control(card: str, ctrl: str, vol: int) -> bool:
    """
    Mengatur volume kontrol ALSA menggunakan kurva perseptual (-M / mapped volume)
    dan memisahkan perintah unmute agar kontrol tanpa playback switch tetap berhasil.
    """
    for cmd in (
        ["amixer", "-M", "-q", "-c", str(card), "sset", ctrl, f"{vol}%"],
        ["amixer", "-q", "-c", str(card), "sset", ctrl, f"{vol}%"],
    ):
        try:
            res = subprocess.run(
                cmd,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=0.8,
            )
            if res.returncode == 0:
                if vol > 0:
                    try:
                        subprocess.run(
                            ["amixer", "-q", "-c", str(card), "sset", ctrl, "unmute"],
                            check=False,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=0.5,
                        )
                    except Exception:
                        pass
                return True
        except Exception:
            pass
    return False


def _set_pipewire_or_pulse_volume(vol: int) -> bool:
    """Mengatur volume sink default via wpctl (PipeWire) atau pactl (PulseAudio)."""
    env = os.environ.copy()
    if "XDG_RUNTIME_DIR" not in env and hasattr(os, "getuid"):
        candidate = f"/run/user/{os.getuid()}"
        if os.path.exists(candidate):
            env["XDG_RUNTIME_DIR"] = candidate

    applied = False
    if shutil.which("wpctl"):
        try:
            res = subprocess.run(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol / 100.0:.2f}"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=0.8,
            )
            if res.returncode == 0:
                applied = True
                if vol > 0:
                    subprocess.run(
                        ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
                        check=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                        timeout=0.5,
                    )
        except Exception:
            pass

    if not applied and shutil.which("pactl"):
        try:
            res = subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{vol}%"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=0.8,
            )
            if res.returncode == 0:
                applied = True
                if vol > 0:
                    subprocess.run(
                        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
                        check=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                        timeout=0.5,
                    )
        except Exception:
            pass

    return applied


def apply_system_volume(vol_percent: int, app_instance=None) -> bool:
    """
    Mengatur volume sistem audio secara presisi tanpa atenuasi ganda:
    - Di Linux dengan hardware mixer ALSA aktif pada device target:
      gunakan `amixer -M` (kurva dB logaritmik agar 20%-100% terdengar proporsional)
      dan biarkan PCM software di 100% agar suara TTS & audio cue tetap jernih dan seragam.
    - Jika mixer hardware tidak tersedia (atau di non-Linux):
      terapkan penskalaan volume PCM digital di Speaker.
    """
    vol = max(0, min(100, int(vol_percent)))

    if platform.system() != "Linux":
        if app_instance and hasattr(app_instance, "speaker") and app_instance.speaker:
            try:
                app_instance.speaker.set_volume(vol)
            except Exception:
                pass
        return True

    cfg_device = "plughw:2,0"
    mixer_name = "Headphone"
    if app_instance and hasattr(app_instance, "cfg") and isinstance(getattr(app_instance, "cfg", None), dict):
        audio_cfg = app_instance.cfg.get("audio", {})
        cfg_device = audio_cfg.get("device", cfg_device)
        mixer_name = audio_cfg.get("mixer", mixer_name)
    elif app_instance and hasattr(app_instance, "speaker") and getattr(app_instance.speaker, "device", None):
        cfg_device = app_instance.speaker.device

    primary_card = _resolve_alsa_card(cfg_device)
    preferred_names = list(
        dict.fromkeys([mixer_name, "Headphone", "Headphones", "PCM", "Master", "Speaker", "Playback", "Digital"])
    )

    hw_applied = False
    with _VOLUME_LOCK:
        if primary_card is not None and shutil.which("amixer"):
            card_ctrls = _get_card_controls(primary_card)
            targets = [c for c in preferred_names if c in card_ctrls] if card_ctrls else preferred_names[:4]
            for ctrl in targets:
                if _set_amixer_control(primary_card, ctrl, vol):
                    hw_applied = True

        if not hw_applied:
            pw_ok = _set_pipewire_or_pulse_volume(vol)
            if primary_card is None and pw_ok:
                hw_applied = True
            elif shutil.which("amixer"):
                for card in _list_sound_cards():
                    if str(card) == str(primary_card):
                        continue
                    card_ctrls = _get_card_controls(card)
                    targets = [c for c in preferred_names if c in card_ctrls]
                    for ctrl in targets:
                        if _set_amixer_control(card, ctrl, vol):
                            hw_applied = True
                if not hw_applied and pw_ok:
                    hw_applied = True

    if app_instance and hasattr(app_instance, "speaker") and app_instance.speaker:
        try:
            # Hindari atenuasi ganda jika hardware mixer pada device target sudah aktif
            app_instance.speaker.set_volume(100 if hw_applied else vol)
        except Exception:
            pass

    return hw_applied


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
        self.volume: float = 1.0  # Skala volume PCM digital (default 100% = tanpa modifikasi)
        self.current_sentence_idx = -1
        self.current_sentence_text = ""
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

    def set_volume(self, vol_percent: int):
        """
        Mengatur skala volume digital PCM.
        Volume 100% = gain 1.0 (maksimum tanpa distorsi).
        Volume diturunkan secara linear dari 1.0 ke 0.05.
        """
        pct = max(0, min(100, int(vol_percent)))
        # Skala linear langsung: 100% = 1.0, 20% = 0.20
        self.volume = max(0.05, pct / 100.0)

    def _scale_pcm(self, pcm_data: bytes) -> bytes:
        """
        Menerapkan penskalaan volume PCM 16-bit.
        Ketika volume = 1.0 (100%), kembalikan data mentah tanpa modifikasi (paling jernih).
        Di bawah 100%: kurangi amplitude secara proporsional.
        """
        if not pcm_data:
            return pcm_data
        if self.volume >= 0.995:
            return pcm_data  # 100% -> tidak dimodifikasi, biarkan hardware kontrol
        try:
            import numpy as np
            samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)
            scaled = np.clip(samples * self.volume, -32767.0, 32767.0).astype(np.int16)
            return scaled.tobytes()
        except Exception:
            return pcm_data

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
                if self.volume < 0.995:
                    try:
                        with wave.open(path, 'rb') as wf:
                            if wf.getsampwidth() == 2 and wf.getcomptype() == 'NONE':
                                nch = wf.getnchannels()
                                fr = wf.getframerate()
                                raw_frames = wf.readframes(wf.getnframes())
                                scaled_frames = self._scale_pcm(raw_frames)
                                proc = subprocess.Popen(
                                    [
                                        'aplay', '-q', '-D', self.device,
                                        '-t', 'raw', '-f', 'S16_LE',
                                        '-c', str(nch), '-r', str(fr), '-'
                                    ],
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL
                                )
                                self._cue = proc
                                if wait:
                                    try:
                                        proc.communicate(input=scaled_frames, timeout=4.0)
                                    except subprocess.TimeoutExpired:
                                        proc.kill()
                                    except Exception:
                                        pass
                                else:
                                    def _feed_cue(p=proc, data=scaled_frames):
                                        try:
                                            if p.stdin:
                                                p.stdin.write(data)
                                                p.stdin.close()
                                        except Exception:
                                            pass
                                    threading.Thread(target=_feed_cue, daemon=True).start()
                                return
                    except Exception:
                        pass

                self._cue = subprocess.Popen(
                    ['aplay', '-q', '-D', self.device, path],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if wait and self._cue:
                    try:
                        self._cue.wait(timeout=4.0)
                    except subprocess.TimeoutExpired:
                        try:
                            self._cue.kill()
                        except Exception:
                            pass
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

    def say(self, sentences: List[str], on_first: Optional[Callable[[], None]] = None,
            on_sentence: Optional[Callable[[int, str], None]] = None) -> bool:
        """
        FR-08 & FR-09: Sintesis dan putar per kalimat secara streaming.
        Sinkronisasi indeks kalimat real-time untuk visualisasi GUI.
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
        self.current_sentence_idx = -1
        self.current_sentence_text = ""
        first_audio_called = False

        if self.has_aplay:
            # Jalur utama Raspberry Pi 4 dengan ALSA aplay per-kalimat agar sinkronisasi visual presisi
            try:
                for idx, sentence in enumerate(sentences):
                    if self._stop.is_set():
                        return False

                    self.current_sentence_idx = idx
                    self.current_sentence_text = sentence
                    if on_sentence:
                        try:
                            on_sentence(idx, sentence)
                        except Exception:
                            pass

                    # Kumpulkan potongan PCM untuk kalimat ini
                    pcm_chunks = []
                    for chunk in self._pcm(sentence):
                        if self._stop.is_set():
                            return False
                        pcm_chunks.append(chunk)

                    if not pcm_chunks:
                        continue

                    if on_first and not first_audio_called:
                        on_first()
                        first_audio_called = True

                    pcm_data = b''.join(pcm_chunks)
                    pcm_data = self._scale_pcm(pcm_data)

                    self._proc = subprocess.Popen(
                        [
                            'aplay', '-q', '-D', self.device,
                            '-t', 'raw', '-f', 'S16_LE',
                            '-c', '1', '-r', str(self.rate), '-'
                        ],
                        stdin=subprocess.PIPE,
                        bufsize=0
                    )

                    try:
                        self._proc.communicate(input=pcm_data)
                    except (BrokenPipeError, OSError):
                        return False

                    if self._stop.is_set():
                        return False

                return not self._stop.is_set()

            except (BrokenPipeError, ValueError, OSError):
                return False
            finally:
                self.playing = False
                self.current_sentence_idx = -1
                self.current_sentence_text = ""
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

                    self.current_sentence_idx = idx
                    self.current_sentence_text = sentence
                    if on_sentence:
                        try:
                            on_sentence(idx, sentence)
                        except Exception:
                            pass

                    print(f" [Audio TTS] Sedang membacakan ({idx + 1}/{len(sentences)}): \"{sentence}\"")
                    pcm_chunks = list(self._pcm(sentence))
                    if not pcm_chunks:
                        continue

                    if on_first and not first_audio_called:
                        on_first()
                        first_audio_called = True

                    pcm_data = b''.join(pcm_chunks)
                    pcm_data = self._scale_pcm(pcm_data)
                    # Putar via wave buffer
                    self._play_pcm_buffer(pcm_data)

                    if self._stop.is_set():
                        return False

                return not self._stop.is_set()
            finally:
                self.playing = False
                self.current_sentence_idx = -1
                self.current_sentence_text = ""

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
        self.current_sentence_idx = -1
        self.current_sentence_text = ""
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
