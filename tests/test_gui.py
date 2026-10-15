import json, os, subprocess, sys, tempfile
HOME = tempfile.mkdtemp(); os.environ["HOME"] = HOME; os.environ["USERPROFILE"] = HOME        # notebook folder -> temp
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen"); os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import renderer; renderer.configure_chromium("compatible")
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PySide6.QtCore import QEventLoop, QTimer
import numpy as np
import aichat, exporter, speech, teacher
from scisketch import MainWindow, PRESETS
from plots import LabPlots

app = QApplication([])
w = MainWindow(app); w.show()


def spin(ms):
    l = QEventLoop(); QTimer.singleShot(ms, l.quit); l.exec()


def until(cond, timeout=60000, step=100):
    t = 0
    while not cond() and t < timeout:
        spin(step); t += step
    assert cond(), "timed out"


def last_assistant():
    return [m["content"] for m in w.chat.history if m["role"] == "assistant"][-1]


def test_all():
    spin(1500)
    # ---- startup state
    assert w.results and w.chat.history[0]["role"] == "assistant" and w.plots.ready
    assert [b.text() for b in w.tab_btns] == ["Experiment", "Teacher", "Export", "Library", "Appearance"]
    for i in range(5):
        w.set_page(i); spin(80)
    w.set_page(0)
    print("startup ok; chat view:", type(w.chat.view).__name__)

    # ---- every quick experiment runs and verifies
    for name in list(PRESETS)[1:]:
        w.preset.setCurrentText(name); w.apply_preset()
        assert w.results and np.isfinite(w.results[w.sel].max_drift) or w.results[w.sel].diverged, name
    assert w.compare and len(w.results) == 3
    print("presets ok:", len(PRESETS) - 1)

    # ---- adaptive layout follows window shape
    w.resize(1500, 900); spin(900); wide_or_stacked = w.plots.mode
    w.canvas.resize(500, 700); w.fig.set_size_inches(5, 7); w._relayout(); spin(200)
    assert w.plots.mode == "stacked", w.plots.mode
    w.fig.set_size_inches(14, 6); w._relayout(); assert w.plots.mode == "wide"
    print("responsive plot layout ok")

    # ---- playback, scrub, live captions
    w.apply_action(dict(system="Kepler", params={"e": 0.7}, dt=0.002, T=20)); w.timer.stop()
    first = [i for i, _ in w.events if i > 0][0]
    w.set_frame(first + 3); assert w.plots.caption(w.frame) and w.plots.cap.get_text()
    w.on_scrub(500); assert abs(w.frame - len(w.t) // 2) <= 2 and not w.timer.isActive()
    w.toggle(); assert w.timer.isActive(); w.tick(); w.tick(); w.toggle()
    print("playback/captions ok")

    # ---- teacher actions (offline)
    for fn in (w.act_explain, w.act_why, w.act_suggest, w.act_lesson, w.act_lesson, w.act_diag):
        n = len(w.chat.history); fn(); spin(400); assert len(w.chat.history) > n, fn.__name__
    w.method_box.setCurrentText("Euler (naive)"); w.run(False); w.act_convergence()
    until(lambda: "Convergence experiment" in last_assistant() or "failed" in last_assistant(), 90000)
    assert "measured order" in last_assistant() or "too close to zero" in last_assistant(), last_assistant()[-300:]
    w.sel = "DOP853 (adaptive)"; w.act_convergence(); assert "adaptive" in last_assistant(); w.sel = "Euler (naive)"
    print("teacher actions ok")

    # ---- quiz flow with scoring
    w.apply_action(dict(system="Nonlinear", compare=True, dt=0.02, T=10)); spin(300)
    w.set_page(1); spin(100); w.act_quiz(); assert len(w.quiz) == 3 and w.q_btns[0].isVisibleTo(w)
    for _ in range(3):
        q = w.quiz[w.quiz_i]; w.answer(q["answer"]); assert "Correct" in w.q_fb.text()
        w.next_q()
    assert w.score == [3, 3] and "3 / 3" in last_assistant()
    w.act_quiz(); w.answer((w.quiz[0]["answer"] + 1) % len(w.quiz[0]["options"])); assert "Not quite" in w.q_fb.text() and w.score == [0, 1]
    print("quiz ok")

    # ---- offline conversation routing (no API key)
    assert not w.chat.ai_ready
    for text, expect in (("why does euler drift?", "Why does the energy drift"), ("Please run kepler with e=0.9", "Setting that up"),
                         ("what's happening here?", "Live teacher"), ("next lesson", "Lesson step")):
        w.chat.box.setText(text); w.chat.on_send(); spin(500)
        assert expect in last_assistant() or any(expect in m["content"] for m in w.chat.history[-4:]), (text, last_assistant()[:120])
    assert abs(w.spins["e"].value() - 0.9) < 1e-9 or w.system.name.startswith("Kepler")
    print("offline conversation ok")

    # ---- AI path with a fake server: streaming, action block executed, failure falls back to the offline teacher
    w.chat.cfg["api_key"] = "k"; w.chat.client.key = "k"; w.chat.refresh_mode(); assert w.chat.mode.text().startswith("AI")
    w.chat.client.stream = lambda msgs: iter(["Great question! ", "Try this:\n```scisketch-run\n", '{"system": "Double", "compare": false}', "\n```\nDone."])
    w.chat.box.setText("show me chaos"); w.chat.on_send(); until(lambda: w.chat.worker is None or not w.chat.worker.isRunning(), 20000); spin(600)
    assert w.system.name.startswith("Double") and "Great question" in " ".join(m["content"] for m in w.chat.history if m["role"] == "assistant")
    def boom(msgs):
        raise aichat.ApiError("429", "rate limited", 30); yield
    w.chat.client.stream = boom
    n = len(w.chat.history); w.chat.box.setText("explain this"); w.chat.on_send()
    until(lambda: any("offline teacher instead" in m["content"] for m in w.chat.history[n:]), 20000); spin(700)
    assert "Live teacher" in " ".join(m["content"] for m in w.chat.history[n:])
    w.chat.cfg["api_key"] = ""; w.chat.client.key = ""; w.chat.refresh_mode()
    print("AI route + automatic offline fallback ok")

    # ---- saving through the UI (dialogs stubbed)
    nb = str(exporter.notebook_dir()); assert nb.startswith(HOME)
    outs = {}
    def fake_save(parent, title, default, filters):
        ext = default.rsplit(".", 1)[-1]; p = os.path.join(HOME, f"out_{len(outs)}.{ext}"); outs[title] = p; return p, filters
    QFileDialog.getSaveFileName = staticmethod(fake_save)
    QMessageBox.warning = staticmethod(lambda *a, **k: None); QMessageBox.information = staticmethod(lambda *a, **k: None)
    w.apply_action(dict(system="Kepler", params={"e": 0.5}, dt=0.01, T=10, compare=True)); spin(300)
    w.save_image(); assert open(outs["Save plot"], "rb").read(8)[:4] == b"\x89PNG"
    w.export_csv(); assert open(outs["Export CSV"]).readline().startswith("t,y0")
    w.copy_plot()
    w.save_chat_pdf(); until(lambda: os.path.exists(outs["Save teacher chat"]) and os.path.getsize(outs["Save teacher chat"]) > 1000, 20000)
    for k in w.d_fmt.values(): k.setChecked(True)
    w.d_notes.setText("My conclusion"); w.save_report()
    made = sorted(os.listdir(nb)); assert any(f.endswith(".docx") for f in made) and any(f.endswith(".pdf") for f in made) and any(f.endswith(".html") for f in made) and any(f.endswith(".md") for f in made), made
    w.save_session(); w.chat.history = []; w.apply_action(dict(system="Nonlinear", dt=0.01, T=5))
    orig = QFileDialog.getOpenFileName; QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (outs["Save lab session"], ""))
    w.open_session(); assert w.system.name.startswith("Kepler") and abs(w.spins["e"].value() - 0.5) < 1e-9 and w.chat.history; QFileDialog.getOpenFileName = orig
    w.v_len.setValue(5); w.v_fps.setCurrentText("24"); w.v_res.setCurrentIndex(2); w.v_nar.setChecked(False)
    w.save_video(); assert not w.btn_video.isEnabled(); until(lambda: w.btn_video.isEnabled(), 120000)
    import imageio_ffmpeg
    nfr, secs = imageio_ffmpeg.count_frames_and_secs(outs["Render video"]); assert 118 <= nfr <= 120 and 4.9 < secs < 5.2, (nfr, secs)
    w.v_fmt.setCurrentIndex(1); w.save_video(); until(lambda: w.btn_video.isEnabled(), 120000)
    w.set_page(3); assert w.lib.count() >= 4 and any(".docx" in w.lib.item(i).text() for i in range(w.lib.count()))
    print("saving ok (png, csv, chat pdf, 4 report formats, session round-trip, mp4 %d frames, gif, library %d files)" % (nfr, w.lib.count()))

    # ---- compatibility fallback when Chromium is unavailable
    w.chat.use_fallback("test fallback"); spin(300)
    assert isinstance(w.chat.view, aichat.FallbackView)
    w.act_explain(); w.chat.view.collect_widgets(lambda x: None)
    got = {}; w.chat.view.diagnostics(lambda t, h: got.update(h=h)); assert got["h"]["fallback"]
    assert "Live teacher" in w.chat.view.toPlainText() and "$" not in w.chat.view.toPlainText().split("Live teacher")[-1][:200]
    print("fallback view ok")

    # ---- speech command building (no audio device needed)
    sent = {}
    class P:
        def __init__(self, cmd, **k): sent["cmd"] = cmd
        def poll(self): return None
        def terminate(self): sent["term"] = True
    real_popen, real_cmd = subprocess.Popen, speech._cmd
    subprocess.Popen = P; speech._cmd = lambda: "espeak-ng"
    sp = speech.Speaker(); assert sp.speak("The period is $T = 2\\pi\\sqrt{L/g}$ and **energy** drifts.")
    assert sent["cmd"][0] == "espeak-ng" and "square root" in sent["cmd"][-1] and "**" not in sent["cmd"][-1]
    sp.stop(); assert sent.get("term")
    subprocess.Popen, speech._cmd = real_popen, real_cmd
    print("speech ok")

    # ---- themes + accent
    for tname in ("Midnight", "Graphite", "Ocean", "Sunset", "Forest", "Light"):
        w.o_theme.setCurrentText(tname); spin(200); assert w.plots.ready
    w.set_accent("#F25CA8"); w.reset_accent()
    w.close()
    print("themes ok")
    print("GUI OK")


if __name__ == "__main__":
    test_all()
