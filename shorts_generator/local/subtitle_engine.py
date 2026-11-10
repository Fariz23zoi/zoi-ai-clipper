
import os
import re

HIGHLIGHTS = {
    "ternyata", "serius", "beneran", "kenapa",
    "akhirnya", "rahasia", "gila", "anjir",
    "kaget", "rp", "juta", "ribu"
}

def ass_time(seconds):
    seconds = max(0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def escape_ass(text):
    return (str(text).replace("\\", r"\\")
            .replace("{", r"\{").replace("}", r"\}"))

def create_ass(segments, clip_start, clip_end, output_path):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,48,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,1,3,1,2,45,45,210,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    for seg in segments:
        start = max(float(seg["start"]), clip_start)
        end = min(float(seg["end"]), clip_end)

        if end <= start:
            continue

        text = escape_ass(seg["text"].strip())
        if not text:
            continue

        words = text.split()
        styled = []
        for word in words:
            clean = re.sub(r"[^a-z0-9]", "", word.lower())
            if clean in HIGHLIGHTS or clean.startswith("rp"):
                styled.append(r"{\c&H0000FFFF&}" + word + r"{\c&H00FFFFFF&}")
            else:
                styled.append(word)

        text = " ".join(styled)
        events.append(
            "Dialogue: 0,"
            + ass_time(start - clip_start) + ","
            + ass_time(end - clip_start)
            + ",Default,,0,0,0,,"
            + text.replace("\n", " ")
        )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")

    return output_path
