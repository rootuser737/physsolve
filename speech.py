"""Offline text-to-speech for the live teacher. No Python dependencies:
Windows -> built-in SAPI via PowerShell, macOS -> `say`, Linux -> espeak-ng / espeak / spd-say (if installed)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def speech_text(md: str, limit=900) -> str:
    """Turn markdown + LaTeX into something that sounds natural when spoken."""
    s = re.sub(r"```.*?```", " ", md, flags=re.S)
    s = re.sub(r"\$\$?(.+?)\$\$?", lambda m: " " + latex_words(m.group(1)) + " ", s, flags=re.S)
    s = re.sub(r"[*_`#>|]", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def latex_words(t: str) -> str:
    for a, b in ((r"\frac", " fraction "), (r"\sqrt", " square root of "), (r"\pi", " pi "), (r"\theta", " theta "),
                 (r"\omega", " omega "), (r"\Delta", " delta "), (r"\approx", " is about "), (r"\cdot", " times "),
                 (r"\times", " times "), (r"\sin", " sine "), (r"\cos", " cosine "), (r"\ln", " natural log "),
                 (r"\lambda", " lambda "), (r"\sim", " is of order "), (r"^2", " squared "), (r"^3", " cubed "),
                 ("=", " equals "), ("+", " plus "), ("\\", " "), ("{", " "), ("}", " "), ("_", " sub ")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def _cmd():
    if sys.platform == "darwin" and shutil.which("say"):
        return "say"
    if os.name == "nt":
        return "powershell"
    for c in ("espeak-ng", "espeak", "spd-say"):
        if shutil.which(c):
            return c
    return None


def available() -> bool:
    return _cmd() is not None


_PS = ("Add-Type -AssemblyName System.Speech; $t = Get-Content -Raw -Encoding UTF8 -LiteralPath '{txt}'; "
       "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Rate = {rate}; {out} $s.Speak($t)")


class Speaker:
    def __init__(self):
        self.proc = None
        self.rate = 0           # -5 .. 5 (Windows scale)

    def speak(self, text: str):
        self.stop()
        c = _cmd()
        text = speech_text(text)
        if not c or not text:
            return False
        if c == "powershell":
            f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
            f.write(text); f.close()
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                   _PS.format(txt=f.name.replace("'", "''"), rate=self.rate, out="")]
        elif c == "say":
            cmd = ["say", "-r", str(175 + 15 * self.rate), text]
        elif c == "spd-say":
            cmd = ["spd-say", "-w", text]
        else:
            cmd = [c, "-s", str(150 + 15 * self.rate), text]
        self.proc = subprocess.Popen(cmd, creationflags=NOWIN, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    @property
    def speaking(self):
        return bool(self.proc and self.proc.poll() is None)


def synth_to_wav(text: str, path: str, rate=0) -> bool:
    """Render speech to a WAV/AIFF file (used for narrated lesson videos). Returns False if unsupported."""
    c, text = _cmd(), speech_text(text, 4000)
    if not c or not text:
        return False
    try:
        if c == "powershell":
            f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
            f.write(text); f.close()
            out = f"$s.SetOutputToWaveFile('{path.replace(chr(39), chr(39) * 2)}');"
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                            _PS.format(txt=f.name.replace("'", "''"), rate=rate, out=out)],
                           creationflags=NOWIN, check=True, capture_output=True, timeout=300)
        elif c == "say":
            subprocess.run(["say", "-o", path, "--data-format=LEI16@22050", "-r", str(175 + 15 * rate), text],
                           check=True, capture_output=True, timeout=300)
        elif c in ("espeak-ng", "espeak"):
            subprocess.run([c, "-w", path, "-s", str(150 + 15 * rate), text], check=True, capture_output=True, timeout=300)
        else:
            return False
        return os.path.isfile(path) and os.path.getsize(path) > 1000
    except Exception:  # noqa: BLE001
        return False
