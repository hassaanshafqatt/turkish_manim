from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
import os
import uuid
import shutil
import subprocess
import re
import tempfile

app = FastAPI()

# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins. In production, specify exact origins.
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
)


def parse_srt(srt_text: str):
    """
    Convert SRT to [(start_time, end_time, text), ...]
    Supports:
    - 00:00:01,500
    - 00:00:01:500
    """
    entries = []

    # Supports both comma and colon milliseconds
    pattern = re.compile(
        r"(\d+)\s+"
        r"(\d{2}:\d{2}:\d{2}[,:]\d{1,3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,:]\d{1,3})"
        r"\s+(.*?)(?=\n\d+\s|\Z)",
        re.DOTALL
    )

    def time_to_seconds(t: str) -> float:
        t = t.replace(",", ":")
        parts = t.split(":")

        # Standard format → HH:MM:SS:mmm   (3 or 4 parts)
        if len(parts) == 3:
            h, m, s = parts
            ms = 0
        elif len(parts) == 4:
            h, m, s, ms = parts
        else:
            raise ValueError(f"Invalid SRT timestamp format: {t}")

        return (
            int(h) * 3600
            + int(m) * 60
            + float(s)
            + (int(ms) / 1000)
        )

    for match in pattern.finditer(srt_text):
        start = time_to_seconds(match.group(2))
        end = time_to_seconds(match.group(3))
        text = match.group(4).replace("\n", " ").strip()
        entries.append((start, end, text))

    return entries


@app.post("/generate-video")
async def generate_video(
    question: str = Form(...),
    options: str = Form(...),  # Dict: {"A": "..", "B": "..."}
    correct_answer: str = Form(...),
    explanation: str = Form(...),
    image: UploadFile = File(None),
    audio: UploadFile = File(None),  # Explanation audio
    question_audio: UploadFile = File(None),  # Question audio
    subtitles: UploadFile = File(None),  # Explanation SRT file
    question_subtitles: UploadFile = File(None),  # Question SRT file
    font_name: str = Form("Pangolin")  # Font name (default: Pangolin)
):
    """
    Accepts form-data containing quiz info, image/audio files,
    and SRT subtitles files for both question and explanation.
    """

    tmp_root = tempfile.gettempdir()
    render_id = str(uuid.uuid4())
    work_dir = os.path.join(tmp_root, render_id)
    os.makedirs(work_dir, exist_ok=True)

    # Save image
    image_path = None
    if image:
        image_filename = image.filename
        image_path_full = os.path.join(work_dir, image_filename)
        with open(image_path_full, "wb") as f:
            shutil.copyfileobj(image.file, f)
        # Store just filename since we run from work_dir
        image_path = image_filename

    # Save explanation audio
    audio_path = None
    if audio:
        audio_filename = audio.filename
        audio_path_full = os.path.join(work_dir, audio_filename)
        with open(audio_path_full, "wb") as f:
            shutil.copyfileobj(audio.file, f)
        # Store just filename since we run from work_dir
        audio_path = audio_filename
    
    # Save question audio
    question_audio_path = None
    if question_audio:
        question_audio_filename = question_audio.filename
        question_audio_path_full = os.path.join(work_dir, question_audio_filename)
        with open(question_audio_path_full, "wb") as f:
            shutil.copyfileobj(question_audio.file, f)
        # Store just filename since we run from work_dir
        question_audio_path = question_audio_filename

    # Parse options -> convert {"A": "London"} to "A) London"
    options_json = json.loads(options)
    formatted_options = [f"{key}) {value}" for key, value in options_json.items()]

    # Parse explanation SRT file
    subtitle_entries = []
    total_explanation_duration = 0
    if subtitles:
        srt_text = (await subtitles.read()).decode("utf-8")
        subtitle_entries = parse_srt(srt_text)
        # Calculate total explanation duration from subtitles
        if subtitle_entries:
            last_subtitle = subtitle_entries[-1]
            total_explanation_duration = last_subtitle[1]  # End time of last subtitle

    # Parse question SRT file
    question_subtitle_entries = []
    total_question_duration = 0
    if question_subtitles:
        question_srt_text = (await question_subtitles.read()).decode("utf-8")
        question_subtitle_entries = parse_srt(question_srt_text)
        # Calculate total question duration from subtitles
        if question_subtitle_entries:
            last_question_subtitle = question_subtitle_entries[-1]
            total_question_duration = last_question_subtitle[1]  # End time of last subtitle

    # Build quiz data JSON
    quiz_data = {
        "question": question,
        "options": formatted_options,
        "correct_answer": correct_answer,
        "explanation": explanation,
        "subtitles": subtitle_entries,  # Explanation subtitles
        "question_subtitles": question_subtitle_entries,  # Question subtitles
        "image_path": image_path,
        "voice_path": audio_path,  # Explanation audio
        "question_audio_path": question_audio_path,  # Question audio
        "font_name": font_name,  # Include font name in quiz data
    }

    # Save quiz.json
    json_path = f"{work_dir}/quiz.json"
    with open(json_path, "w") as f:
        json.dump(quiz_data, f, indent=4)

    # Copy font file to work directory if it exists
    font_source = "QMR.ttf"
    if os.path.exists(font_source):
        font_dest = os.path.join(work_dir, "QMR.ttf")
        shutil.copy2(font_source, font_dest)
    
    # Copy hand SVG file to work directory if it exists
    hand_svg_source = os.path.join("MANIM", "assets", "hand-vector.svg")
    if os.path.exists(hand_svg_source):
        hand_svg_dest = os.path.join(work_dir, "hand-vector.svg")
        shutil.copy2(hand_svg_source, hand_svg_dest)
    
    # Copy correct sound file to work directory if it exists
    # Also reduce volume by 50% (50% of original) using ffmpeg if available
    correct_sound_source = os.path.join("MANIM", "assets", "correct.wav")
    if os.path.exists(correct_sound_source):
        correct_sound_dest = os.path.join(work_dir, "correct.wav")
        # Try to reduce volume by 50% using ffmpeg (if available)
        # -6.02 dB = 50% volume (50% reduction)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-i", correct_sound_source,
                    "-af", "volume=-6.02dB",
                    "-y",  # Overwrite output file
                    correct_sound_dest
                ],
                check=True,
                capture_output=True
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            # If ffmpeg is not available or fails, just copy the file as-is
            shutil.copy2(correct_sound_source, correct_sound_dest)
    
    # Copy whoosh sound file for question slide-in (reduced by -8 dB)
    whoosh_sound_source = os.path.join("MANIM", "assets", "whoosh.mp3")
    if os.path.exists(whoosh_sound_source):
        whoosh_sound_dest = os.path.join(work_dir, "whoosh.mp3")
        # Try to reduce volume by -8 dB using ffmpeg (if available)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-i", whoosh_sound_source,
                    "-af", "volume=-8dB",
                    "-y",  # Overwrite output file
                    whoosh_sound_dest
                ],
                check=True,
                capture_output=True
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            # If ffmpeg is not available or fails, just copy the file as-is
            shutil.copy2(whoosh_sound_source, whoosh_sound_dest)
    
    # Copy swipe sound file for options slide-in (reduced by -12 dB)
    swipe_sound_source = os.path.join("MANIM", "assets", "swipe.wav")
    if os.path.exists(swipe_sound_source):
        swipe_sound_dest = os.path.join(work_dir, "swipe_options.wav")
        # Try to reduce volume by -12 dB using ffmpeg (if available)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-i", swipe_sound_source,
                    "-af", "volume=-12dB",
                    "-y",  # Overwrite output file
                    swipe_sound_dest
                ],
                check=True,
                capture_output=True
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            # If ffmpeg is not available or fails, just copy the file as-is
            shutil.copy2(swipe_sound_source, swipe_sound_dest)
    
    
    # Write Manim scene
    manim_script_path = f"{work_dir}/quiz_scene.py"
    with open(manim_script_path, "w", encoding="utf-8") as f:
        f.write(MANIM_SCRIPT)

    # Render with Manim
    output_path = os.path.join(work_dir, "media", "QuizAnimation.mp4")


    try:
        subprocess.run(
    [
        "manim",
        "-qk",
        manim_script_path,
        "QuizAnimation",
        "--custom_folders",
        "--media_dir",
        f"{work_dir}/media"
    ],
    check=True,
    cwd=work_dir                   # <── FIX
)

    except Exception as e:
        return {"error": str(e)}

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename="quiz_video.mp4"
    )


# --------------------- MANIM SCRIPT (UNCHANGED) ---------------------
MANIM_SCRIPT = r'''
from manim import *
from manim_fonts import *
import json
import os
import textwrap
import numpy as np
import re
import shutil
import platform

# -------------------- VERTICAL 9:16 CONFIG --------------------
config.pixel_height = 1920
config.pixel_width = 1080
config.frame_height = 16  # Manim units
config.frame_width = 9

# -------------------- LaTeX CONFIGURATION (MiKTeX) --------------------
# Configure Manim to use MiKTeX if available
def find_miktex_executable(executable_name):
    """Find MiKTeX executable in common installation paths."""
    if platform.system() == "Windows":
        # Common MiKTeX installation paths on Windows
        username = os.getenv("USERNAME", os.getenv("USER", ""))
        miktex_paths = [
            r"C:\Program Files\MiKTeX\miktex\bin\x64",
            r"C:\Program Files (x86)\MiKTeX\miktex\bin\x64",
            r"C:\Program Files\MiKTeX\miktex\bin",
            r"C:\Program Files (x86)\MiKTeX\miktex\bin",
            os.path.join(os.getenv("LOCALAPPDATA", ""), r"Programs\MiKTeX\miktex\bin\x64"),
            os.path.join(os.getenv("APPDATA", ""), r"MiKTeX\miktex\bin\x64"),
        ]
        
        # Check if executable is in PATH first
        path_exe = shutil.which(executable_name)
        if path_exe:
            return path_exe
        
        # Check MiKTeX installation paths
        for miktex_path in miktex_paths:
            if miktex_path and os.path.exists(miktex_path):
                exe_path = os.path.join(miktex_path, f"{executable_name}.exe")
                if os.path.exists(exe_path):
                    return exe_path
    
    # For Linux/Mac or if not found, return None (use system default)
    return shutil.which(executable_name)

# Try to find MiKTeX executables
latex_exe = find_miktex_executable("latex")
dvisvgm_exe = find_miktex_executable("dvisvgm")
dvipng_exe = find_miktex_executable("dvipng")

# Configure Manim to use MiKTeX if found
if latex_exe:
    # Set LaTeX executable path
    try:
        config.tex_template.latex_compiler = latex_exe
        print(f"Using MiKTeX LaTeX compiler: {latex_exe}")
    except Exception as e:
        print(f"Warning: Could not set LaTeX compiler: {e}")
else:
    print("MiKTeX not found, using system default LaTeX")

if dvisvgm_exe:
    try:
        config.tex_template.dvisvgm = dvisvgm_exe
        print(f"Using MiKTeX dvisvgm: {dvisvgm_exe}")
    except Exception as e:
        print(f"Warning: Could not set dvisvgm: {e}")

if dvipng_exe:
    try:
        config.tex_template.dvipng = dvipng_exe
        print(f"Using MiKTeX dvipng: {dvipng_exe}")
    except Exception as e:
        print(f"Warning: Could not set dvipng: {e}")

# -------------------- FONT CONFIGURATION --------------------
# Font name will be read from quiz_data, default to "Pangolin"

def wrap_text(text: str, max_chars: int):
    # Preserve existing newlines, wrap each paragraph separately
    paragraphs = text.split("\n")
    wrapped = [textwrap.fill(p, width=max_chars) for p in paragraphs]
    return "\n".join(wrapped)

def convert_simplified_math_to_latex(math_content: str) -> str:
    """
    Convert simplified math syntax to proper LaTeX.
    Supports:
    - frac(a)(b) -> \\frac{a}{b}
    - dfrac(a)(b) -> \\dfrac{a}{b}
    Handles nested parentheses correctly.
    Only processes simplified syntax, leaves proper LaTeX unchanged.
    """
    result = []
    i = 0
    while i < len(math_content):
        # Only process if it's NOT already proper LaTeX (no backslash before frac/dfrac)
        # Check if we're at the start of frac(...)(...) or dfrac(...)(...)
        # Make sure there's no backslash before it (unless it's escaped)
        is_simplified_frac = False
        func_name = None
        start_pos = None
        
        if i + 5 <= len(math_content) and math_content[i:i+5] == 'frac(':
            # Check if there's a backslash before (shouldn't be for simplified syntax)
            if i == 0 or (i > 0 and math_content[i-1] != '\\'):
                is_simplified_frac = True
                func_name = 'frac'
                start_pos = i + 5  # After "frac("
        elif i + 6 <= len(math_content) and math_content[i:i+6] == 'dfrac(':
            # Check if there's a backslash before
            if i == 0 or (i > 0 and math_content[i-1] != '\\'):
                is_simplified_frac = True
                func_name = 'dfrac'
                start_pos = i + 6  # After "dfrac("
        
        if is_simplified_frac:
            # Find numerator: content between first ( and matching )
            paren_count = 1
            num_end = start_pos
            while num_end < len(math_content) and paren_count > 0:
                if math_content[num_end] == '(':
                    paren_count += 1
                elif math_content[num_end] == ')':
                    paren_count -= 1
                num_end += 1
            
            if paren_count == 0:
                numerator = math_content[start_pos:num_end-1]  # Exclude closing )
                
                # Check if next is ")(" for denominator
                if num_end < len(math_content) - 1 and math_content[num_end:num_end+2] == ')(':
                    denom_start = num_end + 2  # After ")("
                    paren_count = 1
                    denom_end = denom_start
                    while denom_end < len(math_content) and paren_count > 0:
                        if math_content[denom_end] == '(':
                            paren_count += 1
                        elif math_content[denom_end] == ')':
                            paren_count -= 1
                        denom_end += 1
                    
                    if paren_count == 0:
                        denominator = math_content[denom_start:denom_end-1]  # Exclude closing )
                        # Convert to LaTeX
                        result.append(f'\\{func_name}{{{numerator}}}{{{denominator}}}')
                        i = denom_end
                        continue
        
        # Not a simplified fraction pattern, add character as-is
        result.append(math_content[i])
        i += 1
    
    return ''.join(result)

def parse_text_with_math(text: str):
    r"""
    Parse text and extract math blocks ($...$ or $$...$$).
    Returns list of tuples: (type, content) where type is 'text' or 'math'
    Handles both inline ($...$) and display ($$...$$) math blocks.
    
    Supports LaTeX math notation including:
    - Fractions: $\frac{a}{b}$, $\dfrac{a}{b}$
    - Simplified fractions: $frac(2)(5)$ -> $\frac{2}{5}$
    - Complex expressions: $x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}$
    - Any standard LaTeX math commands
    """
    segments = []
    # Match $$...$$ (display math) or $...$ (inline math)
    # The pattern captures everything between $ delimiters, including braces for fractions
    pattern = r'\$\$([^\$]+)\$\$|\$([^\$]+)\$'
    
    last_end = 0
    for match in re.finditer(pattern, text):
        # Add text before math block (only if non-empty)
        if match.start() > last_end:
            text_before = text[last_end:match.start()]
            if text_before:  # Only add if not empty
                segments.append(('text', text_before))
        
        # Add math block (remove $ delimiters)
        math_content = match.group(1) or match.group(2)  # $$...$$ or $...$
        if math_content:  # Only add if not empty
            # Convert simplified syntax to LaTeX
            math_content = convert_simplified_math_to_latex(math_content)
            segments.append(('math', math_content))
        last_end = match.end()
    
    # Add remaining text (only if non-empty)
    if last_end < len(text):
        text_after = text[last_end:]
        if text_after:  # Only add if not empty
            segments.append(('text', text_after))
    
    # If no math blocks found, return single text segment
    if not segments:
        segments.append(('text', text))
    
    return segments

def create_mixed_text_group(segments, font_size, color, font_family, stroke_width=0, stroke_color=None, fill_opacity=1.0, max_width=None):
    """
    Create a VGroup containing Text and MathTex elements for mixed content.
    Returns VGroup with horizontally arranged text and math elements.
    
    MathTex supports full LaTeX math notation including:
    - Fractions: \\frac{a}{b}, \\dfrac{a}{b}
    - Subscripts/superscripts: x^2, x_i
    - Square roots: \\sqrt{x}, \\sqrt[n]{x}
    - Greek letters: \\alpha, \\beta, \\pi
    - And all other standard LaTeX math commands
    
    Args:
        max_width: Optional maximum width for text wrapping. If provided, the entire group
                   will be constrained to this width. Individual Text elements are NOT
                   constrained separately to allow continuous text flow.
    """
    elements = []
    
    # First, combine all text segments into a single Text element for continuous flow
    # Math blocks remain separate
    text_parts = []
    has_math = False
    
    for seg_type, content in segments:
        if seg_type == 'math':
            has_math = True
            break
        else:
            text_parts.append(content)
    
    if not has_math and text_parts:
        # No math blocks - create single Text element and use set_max_width for proper wrapping
        combined_text = ''.join(text_parts)
        text_elem = Text(
            combined_text,
            font_size=font_size,
            color=color,
            font=font_family,
            stroke_width=stroke_width,
            stroke_color=stroke_color if stroke_color else color,
            fill_opacity=fill_opacity
        )
        # CRITICAL: Use set_max_width() immediately after creation for actual rendered width wrapping
        if max_width is not None:
            text_elem.set_max_width(max_width)
        return text_elem
    
    # Has math blocks - create mixed group with MathTex rendering
    # Create Text elements for text segments and MathTex for math blocks
    elements = []
    text_buffer = []
    
    for seg_type, content in segments:
        if seg_type == 'math':
            # Flush accumulated text - create Text element with set_max_width
            if text_buffer:
                text_content = ''.join(text_buffer)
                text_elem = Text(
                    text_content,
                    font_size=font_size,
                    color=color,
                    font=font_family,
                    stroke_width=stroke_width,
                    stroke_color=stroke_color if stroke_color else color,
                    fill_opacity=fill_opacity
                )
                # Apply set_max_width immediately for proper wrapping
                if max_width is not None:
                    text_elem.set_max_width(max_width)
                elements.append(text_elem)
                text_buffer = []
            
            # Create MathTex element for math block
            try:
                try:
                    math_elem = MathTex(content, font_size=font_size, color=color)
                except TypeError:
                    math_elem = MathTex(content, color=color)
                    scale_factor = font_size / 36.0
                    math_elem.scale(scale_factor)
                elements.append(math_elem)
            except Exception as e:
                print(f"MathTex error for '{content}': {e}, falling back to Text")
                text_elem = Text(
                    content,
                    font_size=font_size,
                    color=color,
                    font=font_family,
                    stroke_width=stroke_width,
                    stroke_color=stroke_color if stroke_color else color,
                    fill_opacity=fill_opacity
                )
                if max_width is not None:
                    text_elem.set_max_width(max_width)
                elements.append(text_elem)
        else:
            text_buffer.append(content)
    
    # Flush remaining text
    if text_buffer:
        text_content = ''.join(text_buffer)
        text_elem = Text(
            text_content,
            font_size=font_size,
            color=color,
            font=font_family,
            stroke_width=stroke_width,
            stroke_color=stroke_color if stroke_color else color,
            fill_opacity=fill_opacity
        )
        # Apply set_max_width immediately for proper wrapping
        if max_width is not None:
            text_elem.set_max_width(max_width)
        elements.append(text_elem)
    
    if not elements:
        return Text("", font_size=font_size, color=color, font=font_family)
    
    if len(elements) == 1:
        return elements[0]
    
    # Arrange elements horizontally - Text elements will wrap internally due to set_max_width
    group = VGroup(*elements)
    group.arrange(RIGHT, buff=0.05, aligned_edge=DOWN)
    
    return group

class QuizAnimation(Scene):
    def construct(self):
        if not os.path.exists("quiz.json"):
            raise Exception("quiz.json not found")

        with open("quiz.json", "r") as f:
            quiz_data = json.load(f)
        
        # Get font name from quiz data, default to "Pangolin"
        font_name = quiz_data.get("font_name", "Pangolin")
        
        # Register font - try Google Fonts first, then local file, then fallback
        self.font_family = "Arial"  # Default fallback
        
        # Try to register from Google Fonts
        try:
            with RegisterFont(font_name) as fonts:
                self.font_family = fonts[0]
                # Run entire scene within font context
                self.show_question_phase(quiz_data)
                self.show_answer_phase(quiz_data)
        except:
            # If Google Fonts fails, try local file path (check if font_name matches a local file)
            local_font_file = f"{font_name}.ttf"
            if os.path.exists(local_font_file):
                try:
                    font_path = os.path.abspath(local_font_file)
                    # Try using the file path directly
                    self.font_family = font_path
                    self.show_question_phase(quiz_data)
                    self.show_answer_phase(quiz_data)
                except:
                    # Use fallback
                    self.show_question_phase(quiz_data)
                    self.show_answer_phase(quiz_data)
            else:
                # Use fallback
                self.show_question_phase(quiz_data)
                self.show_answer_phase(quiz_data)

    def show_question_phase(self, data):
        # IMAGE - full screen background
        if data.get("image_path"):
            try:
                img = ImageMobject(data["image_path"])
                # Scale to cover full height while maintaining aspect ratio
                img.set_height(config.frame_height)
                # If image is wider than frame, scale down to fit width
                if img.width > config.frame_width:
                    scale_factor = config.frame_width / img.width
                    img.scale(scale_factor)
                img.move_to(ORIGIN)
                # Add image first so it's in the background
                self.add(img)
            except Exception as e:
                # If image fails, log but continue
                print(f"Image loading error: {e}")
                pass

        # QUESTION - Material Design card with Turkish title
        # 40px margin = 40/1080 * 9 = ~0.33 Manim units
        margin_units = 0.4  # 40px equivalent
        text_padding = 0.3  # Padding inside box
        
        # Create box first (centered with margins)
        # Use font constant
        question_title = Text("Soru", font_size=40, color="#212121", weight=BOLD, font=self.font_family)
        
        # Calculate box width first
        box_width = config.frame_width - 2 * margin_units
        # Text width should be box width minus padding on both sides
        text_max_width = box_width - 2 * text_padding
        
        # Calculate full question text size to properly size the box
        # If subtitles exist, simulate the layout; otherwise use full text
        question_subtitles = data.get("question_subtitles", [])
        
        if question_subtitles:
            # With subtitles: create all segments and arrange them to calculate total size
            question_text_lines = VGroup()
            last_line = None
            
            for i, (start, end, text) in enumerate(question_subtitles):
                subtitle_segments = parse_text_with_math(text)
                line = create_mixed_text_group(
                    subtitle_segments,
                    font_size=40,
                    color="#212121",
                    font_family=self.font_family,
                    stroke_width=1.0,  # Lighter stroke
                    stroke_color="#212121",
                    fill_opacity=1.0
                )
                line.set_max_width(text_max_width)
                
                if i == 0:
                    # First line
                    question_text_lines.add(line)
                    last_line = line
                else:
                    # Try to position to the right first
                    line.next_to(last_line, RIGHT, buff=0.1, aligned_edge=UP)
                    right_edge = line.get_right()[0]
                    max_right = box_width - text_padding
                    
                    if right_edge > max_right:
                        # Would overflow - position below
                        line.next_to(last_line, DOWN, buff=0.2, aligned_edge=LEFT)
                    question_text_lines.add(line)
                    last_line = line
            
            # Arrange all lines to get total height
            if len(question_text_lines) > 1:
                question_text_lines.arrange(DOWN, buff=0.2, aligned_edge=LEFT)
            full_question_text = question_text_lines
        else:
            # No subtitles: create full question text
            question_segments = parse_text_with_math(data["question"])
            full_question_text = create_mixed_text_group(
                question_segments,
                font_size=40,
                color="#212121",
                font_family=self.font_family,
                stroke_width=1.0,  # Lighter stroke
                stroke_color="#212121",
                fill_opacity=1.0
            )
            full_question_text.set_max_width(text_max_width)
        
        # Position title first to calculate box height
        # We'll position everything relative to the box after it's created
        # First, create a temporary group to estimate total height
        temp_group = VGroup(question_title, full_question_text)
        temp_group.arrange(DOWN, buff=0.25, aligned_edge=LEFT)
        
        # Minimal white box for question (smaller, with 40px margins, centered)
        question_box = RoundedRectangle(
            width=box_width,
            height=temp_group.height + 0.5,
            corner_radius=0.15,
            fill_color=WHITE,
            fill_opacity=0.95,
            stroke_width=0
        )
        # Center box horizontally, position near top
        box_y = config.frame_height / 2 - 0.6 - question_box.height / 2
        question_box.move_to([0, box_y, 0])
        
        # Position title inside box, left-aligned with padding
        question_title.move_to([
            question_box.get_left()[0] + text_padding + question_title.width / 2,
            question_box.get_top()[1] - 0.25 - question_title.height / 2,
            0
        ])
        
        # Question text will be positioned and animated with subtitles (similar to explanation)
        # No need to position it here - it will be done in the subtitle animation
        
        # Subtle shadow effect
        question_shadow = RoundedRectangle(
            width=question_box.width,
            height=question_box.height,
            corner_radius=0.15,
            fill_color=BLACK,
            fill_opacity=0.1,
            stroke_width=0
        )
        question_shadow.move_to(question_box.get_center() + 0.05 * DOWN + 0.05 * RIGHT)

        # Animate question box sliding in from left (with title only, text will animate separately)
        question_box_start = question_box.copy()
        question_shadow_start = question_shadow.copy()
        question_title_start = question_title.copy()
        
        # Position off-screen to the left
        slide_distance = config.frame_width + question_box.width
        question_box_start.shift(LEFT * slide_distance)
        question_shadow_start.shift(LEFT * slide_distance)
        question_title_start.shift(LEFT * slide_distance)
        
        # Add in starting position
        self.add(question_shadow_start, question_box_start, question_title_start)
        
        # Play whoosh sound when question slides in
        if os.path.exists("whoosh.mp3"):
            try:
                self.add_sound("whoosh.mp3")
            except Exception as e:
                print(f"Whoosh sound loading error: {e}")
        
        # Animate sliding in
        self.play(
            question_shadow_start.animate.move_to(question_shadow.get_center()),
            question_box_start.animate.move_to(question_box.get_center()),
            question_title_start.animate.move_to(question_title.get_center()),
            run_time=0.6
        )
        
        # Replace with final positions
        self.remove(question_shadow_start, question_box_start, question_title_start)
        self.add(question_shadow, question_box, question_title)
        
        # Play question audio if provided
        if data.get("question_audio_path"):
            try:
                self.add_sound(data["question_audio_path"])
            except Exception as e:
                print(f"Question audio loading error: {e}")
        
        # Animate question text - exactly like explanation text but with lighter stroke and no hand
        question_subtitles = data.get("question_subtitles", [])
        question_text_segments_list = []  # Track all segments for later reference
        
        # Calculate question text positioning (within question box)
        question_text_max_width = text_max_width
        question_initial_y = question_title.get_bottom()[1] - 0.25  # Below title
        
        if question_subtitles:
            # Track the last text element for positioning
            last_question_text = None
            
            for i, (start, end, text) in enumerate(question_subtitles):
                duration = max(0.1, end - start)
                
                # Create text for this subtitle with lighter stroke - support math blocks
                subtitle_segments = parse_text_with_math(text)
                new_question_line = create_mixed_text_group(
                    subtitle_segments,
                    font_size=40,
                    color="#212121",
                    font_family=self.font_family,
                    stroke_width=1.0,  # Lighter stroke (vs 2.5 for explanation)
                    stroke_color="#212121",  # Same color as fill
                    fill_opacity=1.0
                )
                new_question_line.set_max_width(question_text_max_width)
                
                # Position: first line below title, subsequent lines continue from previous
                is_new_line = False
                
                if i == 0:
                    # First subtitle - position below title, left-aligned with padding
                    new_question_line.next_to(question_title, DOWN, buff=0.25, aligned_edge=LEFT)
                    new_question_line.shift(RIGHT * (question_box.get_left()[0] + text_padding - new_question_line.get_left()[0]))
                else:
                    # Continue from previous text - try to position to the right first
                    new_question_line.next_to(last_question_text, RIGHT, buff=0.1, aligned_edge=UP)
                    
                    # Check if it would overflow the width (respecting padding)
                    right_edge = new_question_line.get_right()[0]
                    max_right = question_box.get_right()[0] - text_padding
                    
                    if right_edge > max_right:
                        # Would overflow - position below instead, left-aligned with padding
                        new_question_line.next_to(last_question_text, DOWN, buff=0.2, aligned_edge=LEFT)
                        # Ensure it respects left margin - always left-align
                        new_question_line.shift(RIGHT * (question_box.get_left()[0] + text_padding - new_question_line.get_left()[0]))
                        is_new_line = True
                    else:
                        # If it fits on same line, ensure it's still left-aligned if needed
                        pass
                
                # Add new_question_line to scene and track it
                self.add(new_question_line)
                question_text_segments_list.append(new_question_line)
                
                # Animate with typewriter effect (no hand animation)
                self.play(
                    Write(new_question_line, run_time=duration),
                )
                
                # Store this line for next positioning
                last_question_text = new_question_line
            
            # Create a VGroup of all question segments for later reference
            if question_text_segments_list:
                question_text = VGroup(*question_text_segments_list)
            else:
                # Fallback to empty text if no segments
                question_text = Text("", font_size=40, color="#212121", font=self.font_family)
        else:
            # No subtitles - create question text normally and animate
            question_segments = parse_text_with_math(data["question"])
            question_text = create_mixed_text_group(
                question_segments,
                font_size=40,
                color="#212121",
                font_family=self.font_family,
                stroke_width=1.0,  # Lighter stroke
                stroke_color="#212121",
                fill_opacity=1.0
            )
            question_text.set_max_width(question_text_max_width)
            question_text.next_to(question_title, DOWN, buff=0.25, aligned_edge=LEFT)
            question_text.shift(RIGHT * (question_box.get_left()[0] + text_padding - question_text.get_left()[0]))
            self.add(question_text)
            
            estimated_duration = len(data["question"]) * 0.05
            estimated_duration = max(1.0, min(estimated_duration, 5.0))  # Between 1-5 seconds
            
            self.play(
                Write(question_text, run_time=estimated_duration),
            )
        
        # Store question elements for later animation
        self.question_box = question_box
        self.question_shadow = question_shadow
        self.question_group = VGroup(question_title, question_text)

        # Wait 500ms after question audio/text animation ends before showing options
        # This delay applies to the entire options segment (animations, text, sounds, everything)
        self.wait(0.5)

        # OPTIONS - Material Design cards with labels
        options_group = VGroup()
        option_boxes = VGroup()
        option_shadows = VGroup()
        option_labels = VGroup()
        
        # Store option elements for later highlighting
        self.option_elements = []
        
        for option in data["options"]:
            # Parse option: "A) London" -> label="A", value="London"
            if ") " in option:
                label, value = option.split(") ", 1)
            else:
                label = option[0] if option else "?"
                value = option[2:] if len(option) > 2 else option
            
            # Label on the left (A, B, C, D)
            label_text = Text(label, font_size=40, color="#212121", weight=BOLD, font=self.font_family)
            
            # Option value text - support math blocks
            value_wrapped = wrap_text(value, 20)
            # Split into lines and process each line for math blocks
            lines = value_wrapped.split("\n")
            value_text_lines = VGroup()
            for line in lines:
                if line.strip():
                    line_segments = parse_text_with_math(line)
                    line_group = create_mixed_text_group(
                        line_segments,
                        font_size=34,
                        color="#212121",
                        font_family=self.font_family
                    )
                    value_text_lines.add(line_group)
            
            # Arrange lines vertically
            if len(value_text_lines) > 0:
                value_text_lines.arrange(DOWN, buff=0.1 * 0.85, aligned_edge=LEFT)
                value_text = value_text_lines
            else:
                # Fallback to empty text
                value_text = Text("", font_size=34, color="#212121", font=self.font_family)
            
            value_text.set_max_width(config.frame_width - 4.0)
            
            # Combine label and value horizontally
            option_row = VGroup(label_text, value_text)
            option_row.arrange(RIGHT, buff=0.3, aligned_edge=UP)
            options_group.add(option_row)
            
            # Minimal white box for each option
            opt_box = RoundedRectangle(
                width=option_row.width + 0.6,
                height=max(label_text.height, value_text.height) + 0.4,
                corner_radius=0.12,
                fill_color=WHITE,
                fill_opacity=0.95,
                stroke_width=0
            )
            opt_box.move_to(option_row.get_center())
            option_boxes.add(opt_box)
            
            # Subtle shadow
            opt_shadow = RoundedRectangle(
                width=option_row.width + 0.6,
                height=max(label_text.height, value_text.height) + 0.4,
                corner_radius=0.12,
                fill_color=BLACK,
                fill_opacity=0.1,
                stroke_width=0
            )
            opt_shadow.move_to(opt_box.get_center() + 0.04 * DOWN + 0.04 * RIGHT)
            option_shadows.add(opt_shadow)
            option_labels.add(label_text)
            
            # Store for later highlighting
            self.option_elements.append({
                "label": label_text,
                "value": value_text,
                "value_text": value,  # Store original text
                "row": option_row,
                "box": opt_box,
                "shadow": opt_shadow,
                "label_str": label.strip()
            })

        # Arrange options in 2x2 grid layout (A B on first row, C D on second row, E below if needed)
        # Group options into rows
        num_options = len(options_group)
        first_row = VGroup()
        second_row = VGroup()
        extra_options = VGroup()
        
        for i, opt_row in enumerate(options_group):
            if i < 2:
                first_row.add(opt_row)
            elif i < 4:
                second_row.add(opt_row)
            else:
                extra_options.add(opt_row)
        
        # Arrange rows horizontally
        if first_row:
            first_row.arrange(RIGHT, buff=1.0)
        if second_row:
            second_row.arrange(RIGHT, buff=1.0)
        if extra_options:
            extra_options.arrange(RIGHT, buff=1.0)
        
        # Position rows vertically - right underneath question box with gap
        # Question box is at top, options should be below it
        question_bottom = self.question_box.get_bottom()[1]
        gap = 0.6  # Gap between question and options
        
        if first_row and second_row:
            first_row_y = question_bottom - gap - first_row.height / 2
            second_row_y = first_row_y - 1.5
            first_row.move_to([0, first_row_y, 0])
            second_row.move_to([0, second_row_y, 0])
            if extra_options:
                extra_options.move_to([0, second_row_y - 1.5, 0])
        elif first_row:
            first_row_y = question_bottom - gap - first_row.height / 2
            first_row.move_to([0, first_row_y, 0])
            if extra_options:
                extra_options.move_to([0, first_row_y - 1.5, 0])
        else:
            if extra_options:
                extra_options_y = question_bottom - gap - extra_options.height / 2
                extra_options.move_to([0, extra_options_y, 0])
        
        # Rebuild options_group with new arrangement
        options_group = VGroup()
        if first_row:
            options_group.add(*first_row)
        if second_row:
            options_group.add(*second_row)
        if extra_options:
            options_group.add(*extra_options)
        
        # Position boxes and shadows to match text
        for i, opt_row in enumerate(options_group):
            # Find corresponding box and shadow
            opt_elem = self.option_elements[i]
            opt_box = opt_elem["box"]
            opt_shadow = opt_elem["shadow"]
            
            opt_box.move_to(opt_row.get_center())
            opt_shadow.move_to(opt_box.get_center() + 0.04 * DOWN + 0.04 * RIGHT)
        
        # Create VGroups for final positions
        option_shadows = VGroup(*[elem["shadow"] for elem in self.option_elements])
        option_boxes = VGroup(*[elem["box"] for elem in self.option_elements])
        
        # Animate options sliding in from left (after question)
        # First: stack them completely overlapping off-screen
        stacked_y = question_bottom - gap - 1.0
        stacked_x = -config.frame_width / 2 - 2.0
        stacked_pos = [stacked_x, stacked_y, 0]
        
        option_shadows_start = VGroup()
        option_boxes_start = VGroup()
        options_group_start = VGroup()
        
        for i, elem in enumerate(self.option_elements):
            shadow_start = elem["shadow"].copy()
            box_start = elem["box"].copy()
            row_start = elem["row"].copy()
            
            # Stack them completely overlapping (same position for all)
            shadow_start.move_to(stacked_pos)
            box_start.move_to(stacked_pos)
            row_start.move_to(stacked_pos)
            
            option_shadows_start.add(shadow_start)
            option_boxes_start.add(box_start)
            options_group_start.add(row_start)
        
        # Add in starting position (stacked and overlapping)
        self.add(option_shadows_start, option_boxes_start, options_group_start)
        
        # Play swipe sound when options slide in
        if os.path.exists("swipe_options.wav"):
            try:
                self.add_sound("swipe_options.wav")
            except Exception as e:
                print(f"Swipe sound loading error: {e}")
        
        # Animate: slide in while stacked (slower)
        slide_in_distance = config.frame_width / 2 + 1.0
        self.play(
            option_shadows_start.animate.shift(RIGHT * slide_in_distance),
            option_boxes_start.animate.shift(RIGHT * slide_in_distance),
            options_group_start.animate.shift(RIGHT * slide_in_distance),
            run_time=0.8  # Slower animation
        )
        
        # Now shuffle to their designated grid positions (slower)
        shuffle_anims = []
        for i, elem in enumerate(self.option_elements):
            target_row = options_group[i] if i < len(options_group) else None
            if target_row:
                target_pos = target_row.get_center()
                shuffle_anims.extend([
                    option_shadows_start[i].animate.move_to(option_shadows[i].get_center()),
                    option_boxes_start[i].animate.move_to(option_boxes[i].get_center()),
                    options_group_start[i].animate.move_to(target_pos),
                ])
        
        self.play(*shuffle_anims, run_time=0.8)  # Slower shuffle animation
        
        # Replace with final positions
        self.remove(option_shadows_start, option_boxes_start, options_group_start)
        self.add(option_shadows, option_boxes, options_group)

        self.wait(5)

    def show_answer_phase(self, data):
        correct_answer = data.get("correct_answer", "").strip()
        correct_option = None
        incorrect_options = []
        
        # Find correct and incorrect options
        if hasattr(self, 'option_elements'):
            for opt_elem in self.option_elements:
                if opt_elem["label_str"] == correct_answer:
                    correct_option = opt_elem
                else:
                    incorrect_options.append(opt_elem)
        
        # Step 1: Question box stays in place (no animation)
        # Step 2: Keep all options visible (no changes needed - they're already visible)
        
        # Step 3: Add explanation text below options
        # Calculate text width (50px margin on each side)
        # 50px = 50/1080 * 9 ≈ 0.42 Manim units
        margin_50px = 0.42
        # Full width minus margins on both sides
        # Frame width is 9 Manim units, so available width = 9 - 2*0.42 = 8.16
        # This ensures text uses the full frame width while maintaining 50px margins on left and right
        # Calculate from frame edges: left edge is at -frame_width/2, right edge is at +frame_width/2
        # Left margin starts at -frame_width/2 + margin_50px = -4.5 + 0.42 = -4.08
        # Right margin ends at +frame_width/2 - margin_50px = +4.5 - 0.42 = +4.08
        # Available width = 4.08 - (-4.08) = 8.16
        exp_text_max_width = config.frame_width - 2 * margin_50px
        
        # Position explanation text below options (always below the last option, which is E)
        last_option = None
        if hasattr(self, 'option_elements') and len(self.option_elements) > 0:
            # Find the last option (E) - it's the last element in the list
            last_option = self.option_elements[-1]
            explanation_y = last_option["row"].get_bottom()[1] - 0.6  # Gap below option
        else:
            explanation_y = 2.0
        
        # Create explanation text area (will use typewriter effect)
        # Use font constant
        explanation_area = Text("", font_size=32, color="#212121", font=self.font_family)
        explanation_area.set_max_width(exp_text_max_width)
        explanation_area.move_to([0, explanation_y, 0])
        explanation_area.to_edge(LEFT, buff=margin_50px)
        
        self.add(explanation_area)

        # Voice / audio will be added when first subtitle starts to sync with text animation

        # Load hand SVG for writing animation
        hand = None
        svg_path = "hand-vector.svg"
        if os.path.exists(svg_path):
            try:
                hand = SVGMobject(svg_path)
                hand.set_height(10)  # Scale appropriately - make it much larger (10x increase)
                # Rotate counter-clockwise (negative angle)

                # Ensure all subpaths are visible
                for submob in hand.submobjects:
                    submob.set_fill(opacity=1.0)
                    submob.set_stroke(width=0)
                hand.set_fill(opacity=1.0)  # Ensure it's fully opaque when visible
                hand.set_stroke(width=0)  # Remove stroke if any
                hand.set_opacity(0)  # Start invisible for fade-in
                # Set z-index to be above all other elements
                hand.set_z_index(1000)  # Very high z-index to appear above everything
                # Position it initially (will be repositioned later)
                hand.move_to([0, 0, 0])
                self.add(hand)
                print(f"Hand SVG loaded successfully: {svg_path}, height: {hand.height}, width: {hand.width}")
            except Exception as e:
                print(f"Hand SVG loading error: {e}")
                import traceback
                traceback.print_exc()
                hand = None
        else:
            print(f"Hand SVG file not found: {svg_path} (current dir: {os.getcwd()})")
            # List files in current directory for debugging
            try:
                files = os.listdir(".")
                print(f"Files in current directory: {[f for f in files if f.endswith('.svg')]}")
            except:
                pass

        # Subtitles - combine all segments into one continuous flowing text
        subtitles = data.get("subtitles", [])
        explanation_text_segments_list = []  # Track all segments for later reference
        
        # Calculate bottom threshold for scrolling: 20px buffer = 20/1080 * 16 ≈ 0.296 Manim units from bottom
        frame_bottom = -config.frame_height / 2  # -8
        bottom_threshold = frame_bottom + (20 / 1080 * 16)  # -8 + 0.296 ≈ -7.704
        
        if subtitles:
            # Track the last text element for positioning
            last_explanation_text = None
            # Track all explanation text elements for scrolling
            explanation_text_elements = []
            
            # Add voice audio when first subtitle starts to sync with animation
            if data.get("voice_path"):
                first_start = subtitles[0][0] if subtitles else 0
                if first_start > 0:
                    self.wait(first_start)
                try:
                    self.add_sound(data["voice_path"])
                except:
                    pass
            
            for i, (start, end, text) in enumerate(subtitles):
                duration = max(0.1, end - start)
                
                # Create text for this subtitle with pen-like stroke - support math blocks
                subtitle_segments = parse_text_with_math(text)
                new_explanation_line = create_mixed_text_group(
                    subtitle_segments,
                    font_size=32,
                    color="#212121",
                    font_family=self.font_family,
                    stroke_width=2.5,  # Thicker stroke for pen-like effect
                    stroke_color="#212121",  # Same color as fill
                    fill_opacity=1.0,
                    max_width=exp_text_max_width
                )
                new_explanation_line.set_max_width(exp_text_max_width)
                
                # Position: first line below last option (E), subsequent lines continue from previous
                is_new_line = False
                should_scroll_up = False
                scroll_amount = 0
                
                if i == 0:
                    # First subtitle - position below last option (E), center-aligned
                    if last_option:
                        new_explanation_line.next_to(last_option["row"], DOWN, buff=0.6)
                    else:
                        new_explanation_line.move_to([0, explanation_y, 0])
                    # Center align on frame
                    new_explanation_line.move_to([0, new_explanation_line.get_center()[1], 0])
                else:
                    # Continue from previous text - try to position to the right first
                    new_explanation_line.next_to(last_explanation_text, RIGHT, buff=0.1, aligned_edge=UP)
                    
                    # Check if it would overflow the width (respecting margin)
                    right_edge = new_explanation_line.get_right()[0]
                    max_right = config.frame_width / 2 - margin_50px
                    
                    if right_edge > max_right:
                        # Would overflow - position below instead, center-aligned
                        new_explanation_line.next_to(last_explanation_text, DOWN, buff=0.2, aligned_edge=ORIGIN)
                        # Center align on frame
                        new_explanation_line.move_to([0, new_explanation_line.get_center()[1], 0])
                        is_new_line = True
                
                # Check if this line would go below the bottom threshold
                if new_explanation_line.get_bottom()[1] < bottom_threshold:
                    should_scroll_up = True
                    scroll_amount = new_explanation_line.height + 0.2
                
                # If we need to scroll up, smoothly move all existing text up
                if should_scroll_up and explanation_text_elements:
                    scroll_anims = []
                    for elem in explanation_text_elements:
                        scroll_anims.append(elem.animate.shift(UP * scroll_amount))
                    if hand is not None:
                        scroll_anims.append(hand.animate.shift(UP * scroll_amount))
                        hand.set_z_index(1000)
                    if scroll_anims:
                        self.play(*scroll_anims, run_time=0.4)
                    if hand is not None:
                        hand.set_z_index(1000)
                    # Recalculate position after scrolling
                    if is_new_line and last_explanation_text:
                        new_explanation_line.next_to(last_explanation_text, DOWN, buff=0.2, aligned_edge=ORIGIN)
                        # Center align on frame
                        new_explanation_line.move_to([0, new_explanation_line.get_center()[1], 0])
                
                # Position hand above text before writing
                if hand is not None:
                    text_top_y = new_explanation_line.get_top()[1]
                    hand_left = hand.get_left()[0]
                    hand_center_x = hand.get_center()[0]
                    hand_height = hand.get_height()
                    pen_nib_offset_from_center = 0.4 * hand_height
                    pen_nib_offset_x = hand_left - hand_center_x
                    pen_nib_offset_y = pen_nib_offset_from_center
                    adjustment = 0.02 * hand_height
                    initial_x = new_explanation_line.get_left()[0] - pen_nib_offset_x
                    initial_y = text_top_y - pen_nib_offset_y - adjustment
                    
                    if i == 0:
                        # First subtitle - position hand and make it visible immediately
                        hand.move_to([initial_x, initial_y, 0])
                        hand.set_opacity(1.0)
                        hand.set_z_index(1000)
                        self.add(hand)
                    else:
                        # Subsequent subtitles - smoothly animate hand to new position
                        if is_new_line:
                            # Moving to a new line - animate smoothly
                            self.play(
                                hand.animate.move_to([initial_x, initial_y, 0]),
                                run_time=0.4,
                                rate_func=smooth
                            )
                        else:
                            # Continuing on same line - quick move
                            self.play(
                                hand.animate.move_to([initial_x, initial_y, 0]),
                                run_time=0.2,
                                rate_func=smooth
                            )
                    
                    # Reset rotation when starting new line
                    if hasattr(hand, '_current_rotation'):
                        hand.rotate(-hand._current_rotation, about_point=hand.get_center())
                        hand._current_rotation = 0
                
                # Animate hand following text with natural writing motion
                if hand is not None:
                    text_left = new_explanation_line.get_left()[0]
                    text_right = new_explanation_line.get_right()[0]
                    start_x = text_left + 0.1
                    end_x = text_right + 0.1
                    
                    hand_left = hand.get_left()[0]
                    hand_center_x = hand.get_center()[0]
                    hand_height = hand.get_height()
                    pen_nib_offset_from_center = 0.4 * hand_height
                    adjustment = 0.02 * hand_height
                    pen_nib_offset_x = hand_left - hand_center_x
                    pen_nib_offset_y = pen_nib_offset_from_center
                    
                    def update_hand_position(mob, alpha):
                        current_x = start_x + (end_x - start_x) * alpha
                        time_val = alpha * duration * 12
                        vertical_offset_1 = 0.02 * np.sin(time_val)
                        vertical_offset_2 = 0.01 * np.sin(time_val * 2.3)
                        vertical_offset = vertical_offset_1 + vertical_offset_2
                        rotation_val = alpha * duration * 10
                        rotation_1 = 1.5 * np.sin(rotation_val) * DEGREES
                        rotation_2 = 0.5 * np.sin(rotation_val * 1.7) * DEGREES
                        rotation_offset = rotation_1 + rotation_2
                        depth_offset = 0.005 * np.sin(time_val * 1.5)
                        adjustment = 0.02 * hand_height
                        hand_y = text_top_y - pen_nib_offset_y - adjustment + vertical_offset
                        hand_x = current_x - pen_nib_offset_x + depth_offset
                        hand.move_to([hand_x, hand_y, 0])
                        current_rotation = getattr(hand, '_current_rotation', 0)
                        rotation_diff = rotation_offset - current_rotation
                        hand.rotate(rotation_diff, about_point=hand.get_center())
                        hand._current_rotation = rotation_offset
                    
                    hand_anim = UpdateFromAlphaFunc(hand, update_hand_position)
                    
                    # Add new_explanation_line to scene right before Write animation
                    self.add(new_explanation_line)
                    explanation_text_elements.append(new_explanation_line)
                    explanation_text_segments_list.append(new_explanation_line)
                    
                    # Write with typewriter effect and animate hand simultaneously
                    self.play(
                        Write(new_explanation_line, run_time=duration, lag_ratio=0.01),
                        hand_anim,
                    )
                    
                    # Ensure hand stays visible
                    hand.set_opacity(1.0)
                    
                    # Final hand position
                    final_y = text_top_y - pen_nib_offset_y - adjustment
                    final_x = end_x - pen_nib_offset_x
                    hand.move_to([final_x, final_y, 0])
                    if hasattr(hand, '_current_rotation'):
                        hand.rotate(-hand._current_rotation, about_point=hand.get_center())
                        hand._current_rotation = 0
                else:
                    # Add new_explanation_line to scene right before Write animation
                    self.add(new_explanation_line)
                    explanation_text_elements.append(new_explanation_line)
                    explanation_text_segments_list.append(new_explanation_line)
                    
                    # Write with typewriter effect (no hand)
                    self.play(
                        Write(new_explanation_line, run_time=duration, lag_ratio=0.01),
                    )
                
                # Store this line for next positioning
                last_explanation_text = new_explanation_line
                
                # Remove the empty placeholder if this is the first line
                if i == 0:
                    self.remove(explanation_area)
            
            # Fade out hand at end of all subtitles
            if hand is not None:
                self.play(FadeOut(hand, run_time=0.3))
            
            # Sound stops automatically when explanation completes (limited by time_span parameter)
            
            # Wait 1 second before showing correct option
            self.wait(1.0)
            
            # After explanation is complete, highlight correct option with green outline and scale it up
            if correct_option:
                # Play correct answer sound
                if os.path.exists("correct.wav"):
                    try:
                        self.add_sound("correct.wav")
                    except Exception as e:
                        print(f"Sound loading error: {e}")
                
                # Get the current position and size of the correct option
                current_pos = correct_option["row"].get_center()
                original_width = correct_option["box"].width
                original_height = correct_option["box"].height
                
                # Scale factor for the correct option (make it larger)
                scale_factor = 1.3  # 30% larger
                new_width = original_width * scale_factor
                new_height = original_height * scale_factor
                
                # Calculate how much space we need - the correct option will grow
                # We need to push other options away to make room
                growth_width = (new_width - original_width) / 2
                growth_height = (new_height - original_height) / 2
                
                # Find which row the correct option is in and adjust spacing
                correct_index = None
                for idx, opt_elem in enumerate(self.option_elements):
                    if opt_elem == correct_option:
                        correct_index = idx
                        break
                
                # Create animations for scaling correct option and adjusting others
                animations = []
                
                # Scale up the correct option's row (text)
                animations.append(correct_option["row"].animate.scale(scale_factor))
                # Scale up the box itself
                animations.append(correct_option["box"].animate.scale(scale_factor))
                # Scale up the shadow
                animations.append(correct_option["shadow"].animate.scale(scale_factor))
                
                # Adjust other options to make room
                # For options in the same row, push them horizontally
                # For options in rows above/below, push them vertically if needed
                for opt_elem in self.option_elements:
                    if opt_elem != correct_option:
                        opt_pos = opt_elem["row"].get_center()
                        correct_pos = correct_option["row"].get_center()
                        
                        # Check if in same row (similar y position)
                        y_diff = abs(opt_pos[1] - correct_pos[1])
                        if y_diff < 0.5:  # Same row
                            # Push horizontally away from correct option
                            if opt_pos[0] < correct_pos[0]:
                                # Option is to the left, push it further left
                                shift_x = -growth_width * 0.5
                            else:
                                # Option is to the right, push it further right
                                shift_x = growth_width * 0.5
                            animations.append(opt_elem["row"].animate.shift(RIGHT * shift_x))
                            animations.append(opt_elem["box"].animate.shift(RIGHT * shift_x))
                            animations.append(opt_elem["shadow"].animate.shift(RIGHT * shift_x))
                        else:
                            # Different row - push vertically if too close
                            if opt_pos[1] > correct_pos[1]:  # Option is above
                                if y_diff < growth_height + 0.3:
                                    animations.append(opt_elem["row"].animate.shift(UP * growth_height * 0.5))
                                    animations.append(opt_elem["box"].animate.shift(UP * growth_height * 0.5))
                                    animations.append(opt_elem["shadow"].animate.shift(UP * growth_height * 0.5))
                            else:  # Option is below
                                if y_diff < growth_height + 0.3:
                                    animations.append(opt_elem["row"].animate.shift(DOWN * growth_height * 0.5))
                                    animations.append(opt_elem["box"].animate.shift(DOWN * growth_height * 0.5))
                                    animations.append(opt_elem["shadow"].animate.shift(DOWN * growth_height * 0.5))
                
                # Animate: scale up correct option and adjust others smoothly
                self.play(
                    *animations,
                    run_time=0.8
                )
                
                # After scaling, get the current position of the scaled box
                scaled_pos = correct_option["row"].get_center()
                scaled_width = correct_option["box"].width
                scaled_height = correct_option["box"].height
                
                # Create a separate outline box for blinking (on top of the scaled box)
                # This will only show the stroke, not affect the box fill
                blink_outline = RoundedRectangle(
                    width=scaled_width,
                    height=scaled_height,
                    corner_radius=0.12,
                    fill_opacity=0,  # Completely transparent fill
                    stroke_color=GREEN,
                    stroke_width=6,
                    stroke_opacity=0  # Start invisible
                )
                blink_outline.move_to(scaled_pos)
                self.add(blink_outline)
                
                # Blink the outline 3 times before it stays
                for blink_count in range(3):
                    # Fade in outline
                    self.play(blink_outline.animate.set_stroke(opacity=1.0), run_time=0.2)
                    # Fade out outline
                    self.play(blink_outline.animate.set_stroke(opacity=0), run_time=0.2)
                
                # Remove blinking outline and add permanent green stroke to the box
                self.remove(blink_outline)
                # Add green stroke to the existing box without changing its fill
                correct_option["box"].set_stroke(color=GREEN, width=6, opacity=1.0)
                # Ensure the box keeps its white fill
                correct_option["box"].set_fill(color=WHITE, opacity=0.95)

        self.wait(2)

    def get_answer_text(self, data):
        for op in data["options"]:
            if op.startswith(data["correct_answer"] + ")"):
                return op.split(") ", 1)[1]
        return ""
'''


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
