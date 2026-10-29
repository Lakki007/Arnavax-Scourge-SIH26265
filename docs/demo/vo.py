import json, soundfile as sf, numpy as np
from kokoro_onnx import Kokoro
k = Kokoro("/tmp/claude-0/tts/kokoro-v1.0.onnx", "/tmp/claude-0/tts/voices-v1.0.bin")
SC = [
 ("hook",     "Every security deposit AICTE holds is checked once. Then it's trusted for ten years. On paper."),
 ("stakes",   "In Haryana, two hundred and one crore rupees of fixed deposits simply weren't in the bank's records."),
 ("baseline", "Today's portals look at a picture. An edited screenshot? Approved."),
 ("brand",    "Scourge asks the bank itself."),
 ("prove",    "AICTE sends a single-use challenge. The institution logs into its own net banking, and the deposit page is fetched in one secure session whose keys are split with AICTE's verifier. So the institution cannot edit what the bank said."),
 ("evidence", "And here, the bank contradicts the paper. A loan, raised against the deposit. Every value traces back to the bank's own bytes, while the PAN and every other account stay hidden."),
 ("act",      "The officer escalates, and the release stays blocked. A fake bank is rejected. And if a bank page can't show liens, Scourge says so. Never a false verified."),
 ("close",    "Scourge. Ask the bank, not the paper."),
]
VOICE, SPEED = "af_heart", 1.0
out = []
for name, text in SC:
    s, sr = k.create(text, voice=VOICE, speed=SPEED, lang="en-us")
    s = np.asarray(s, dtype="float32")
    sf.write(f"vo_{name}.wav", s, sr)
    out.append({"name": name, "text": text, "dur": round(len(s) / sr, 2), "sr": sr})
    print(name, out[-1]["dur"])
json.dump(out, open("vo.json", "w"), indent=1)
print("total speech", round(sum(o["dur"] for o in out), 1))
