# Design Document

## Overview

هذا التصميم يحوّل متطلبات `voice-control-and-drawing-ux` إلى خطة تقنية ملموسة مبنية على الكود القائم في `wall_climber`. الميزة تضيف **طبقة تحكّم صوتي** فوق الواجهة والـ backend الموجودين، وتبسّط تدفّق الكتابة/الرسم، وتنظّف إعدادات السكتش، وتعالج مشكلتين في جودة الرسم (تشوّه الوجوه + الخطوط الرفيعة غير القابلة للرسم).

المبدأ التصميمي الأساسي: **إعادة استخدام المسارات الموجودة، لا استبدالها.** كل التدفّقات الجديدة (الأوامر الصوتية، الكتابة المُملاة، الرسم من المكتبة، زر الإجراء الواحد) تنتهي عند نفس نقاط النهاية الموجودة فعلاً:
- تبديل الوضع → `WebBackendNode.switch_mode()` → `ACTIVE_MODE_TOPIC`.
- المعاينة → `POST /api/preview` (`generate_preview` → `preview_sketch_centerline` / `_json_text_preview_response`).
- الرسم → `POST /api/draw` (`draw_cached_preview` → `publish_execution_plan` → `PRIMITIVE_PATH_PLAN_TOPIC`).

الميزة موزّعة على ثلاث طبقات:

| الطبقة | الملفات الرئيسية | ما يتغيّر |
|--------|------------------|-----------|
| **Web UI (frontend)** | `src/wall_climber/web/index.html` | زر ميكروفون، لوحة النص المُملى، عدّاد الإلغاء، سلايدر فلترة الخطوط، تنظيف إعدادات السكتش، زر إجراء واحد |
| **Web Backend (FastAPI)** | `src/wall_climber/wall_climber/web_server.py` + ملف جديد `voice_transcribe.py` + `draw_library.py` | نقطة `/api/voice/transcribe`، نقطة `/api/draw_library/{id}`، تمرير `thin_line_min_width_mm` للـ pipeline |
| **Sketch Pipeline** | `image_pipeline/sketch_centerline.py` + `_preprocess.py` + ملف جديد `_thin_line_filter.py` + `_face_regions.py` | فلتر عرض الخط، معالجة مناطق الوجه |

> ملاحظة توافق: المتطلبات تشير لمجلد `assets/draw_library/`، والـ `manifest.example.json` موجود فعلاً بالشكل `{version, entries:[{id,name,file,default_mode,description}]}`. سنبني `Draw_Library_Service` حول هذا الشكل تماماً.

### قرار تقني رئيسي: محرك التعرّف على الكلام (Speech-to-Text)

الخيارات المتاحة:

| الخيار | الدقة الإنجليزية | يعمل offline | دعم المتصفحات | تكلفة التكامل |
|--------|------------------|--------------|----------------|----------------|
| **Browser Web Speech API** | جيدة | ❌ لا (Chrome يرسل الصوت لخادم Google) | Chrome/Edge فقط | صفر (JS فقط) |
| **faster-whisper (self-hosted)** | ممتازة (نموذج Whisper) | ✅ نعم | كل المتصفحات | متوسطة (endpoint + نموذج) |

**القرار: نموذج هجين بأولوية للخادم الذاتي (faster-whisper).** السبب: المستخدم طلب صراحةً "إشي فعلاً يلتقط الإنجليزي صح" و"مشروع مثالي على github". `faster-whisper` (إعادة تنفيذ Whisper بـ CTranslate2) هو الأنسب: دقة عالية، يعمل offline داخل الـ devcontainer، CPU-friendly مع نموذج `base.en` أو `small.en`، ومتاح كحزمة pip. الـ backend يعرّض `POST /api/voice/transcribe` يستقبل صوت WebM/WAV ويرجّع النص. الواجهة تسجّل الصوت بـ `MediaRecorder` وترسله للـ endpoint.

الـ **Web Speech API** يُستخدم كـ fallback اختياري صفري-الاعتماديات: إذا فشل الـ backend transcription (النموذج غير مثبّت) وكان المتصفح يدعم `SpeechRecognition`، تتحوّل الواجهة تلقائياً له. هذا يضمن أن الميزة تظل قابلة للعرض حتى لو لم يُثبّت النموذج.

> اعتمادية جديدة: `faster-whisper` تُضاف لـ `install_requires` في `setup.py`. تنزيل أوزان النموذج يتم مرة واحدة عند أول استخدام (يُخزّن في كاش المستخدم). إذا تعذّر التنزيل/التثبيت، يبقى الـ endpoint يرجّع 503 والواجهة تسقط للـ Web Speech API.

## Architecture

### مخطّط التدفّق الصوتي العام

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Web UI (index.html)                          │
│                                                                       │
│  [🎤 voice-capture-btn]                                               │
│        │ MediaRecorder → audio blob (webm/opus)                       │
│        ▼                                                              │
│   VoiceController (new JS module)                                     │
│        │ POST /api/voice/transcribe (multipart audio)                 │
│        ▼                                                              │
│   transcript text ──► VoiceCommandRouter (new JS)                     │
│        │                                                              │
│        ├── "text mode" / "draw mode" ──► setMode() ──► POST /api/mode │
│        ├── (text mode, free text) ─────► TextDictationController      │
│        │        └─► show transcript + 5s countdown                    │
│        │            ├─ click transcript → CANCEL (no write)           │
│        │            └─ countdown→0 → single-action commit (preview+draw)│
│        └── (draw mode) "draw picture number N" ──► POST /api/draw_library/N │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Web Backend (web_server.py)                        │
│                                                                       │
│  POST /api/voice/transcribe ──► voice_transcribe.transcribe_audio()   │
│  POST /api/mode ────────────────► switch_mode() ──► ACTIVE_MODE_TOPIC │
│  POST /api/preview ─────────────► generate_preview() (existing)       │
│  POST /api/draw ────────────────► draw_cached_preview() (existing)    │
│  POST /api/draw_library/{id} ───► draw_library.resolve() → preview+draw│
└─────────────────────────────────────────────────────────────────────┘
```

### مخطّط تدفّق الكتابة المُملاة (Requirement 3)

```
transcript (not a command)
   │
   ▼
TextDictationController.present(transcript)
   │  render transcript verbatim in #voice-transcript
   │  start 5s countdown (#voice-countdown)
   │
   ├── user clicks #voice-transcript  ──────────────► cancelPending()  → NO write, text stays editable
   ├── new transcript arrives          ──────────────► cancelPending() → present(new)  (restart 5s)
   └── countdown reaches 0             ──────────────► commitText(transcript)
                                                          │
                                                          ▼
                                            single action: POST /api/preview (text)
                                                          │  → preview_id
                                                          ▼
                                                        POST /api/draw {preview_id}
```

### الأدلّة المعمارية المستخلَصة من الكود

- **الوضع الحالي لتبديل الوضع**: `switch_mode()` يرفض بـ 409 عندما `cable_executor_status == 'running'`، ويرفع 503 إذا الـ runtime مش جاهز. كل الأوامر الصوتية لتبديل الوضع تمرّ عبر نفس المسار → نفس سلوك الرفض تلقائياً (يغطّي AC 2.5).
- **الوضع الحالي لتدفّق المعاينة→الرسم**: الواجهة حالياً خطوتان (`previewText()` ثم `drawText()`، و`previewSketchCenterlineFile()` ثم `drawUploadedFile()`)، ويحرسها `state.currentPreviewId` و`state.previewDirty`. زر الإجراء الواحد يربط الخطوتين في دالة واحدة `commitText()` / `commitSketch()`.
- **لا يوجد مفهوم لعرض الخط في الـ pipeline**: تأكّدنا أن `sketch_centerline.py` يستخرج **مركز الخط (centerline/skeleton)** فقط ويتجاهل كل معلومات السماكة. لذلك "فلتر الخطوط الرفيعة" يجب أن يقيس السماكة **قبل** التنحيف (skeletonization)، من القناع الثنائي (binary mask) عبر **distance transform** — هذه إضافة جديدة بالكامل.
- **لا يوجد كشف وجه**: لا يوجد أي معالجة خاصة بالوجه؛ الأشكال المملوءة تُنحّف لمحورها الأوسط. تشوّه الوجه ناتج عن أن العيون/الفم مناطق صغيرة عالية التفاصيل تنهار عند التنحيف. الحل: كشف منطقة الوجه ومعالجتها بمعاملات ألطف.

## Components and Interfaces

### المكوّن 1: VoiceController (Frontend — JS داخل index.html)

مسؤول عن التقاط الصوت وتحويله لنص.

```javascript
// Pseudo-interface (vanilla JS module pattern matching existing index.html style)
const VoiceController = {
  state: { recording: false, mediaRecorder: null, chunks: [] },

  // بدء/إيقاف التسجيل عبر MediaRecorder
  async toggle(),                    // يبدّل التسجيل عند الضغط على الزر
  async startRecording(),            // getUserMedia({audio}) + MediaRecorder
  async stopRecording(),             // يجمّع الـ chunks → Blob → transcribe()

  // إرسال الصوت للـ backend، مع fallback للـ Web Speech API
  async transcribe(audioBlob) -> string,   // POST /api/voice/transcribe
  async transcribeViaWebSpeech() -> string, // fallback إذا فشل الـ backend

  onTranscript(text),                // callback → VoiceCommandRouter.route(text)
};
```

سلوك مفصّل:
- زر `#voice-capture-btn` يظهر فقط في `MODE_TEXT` أو `MODE_DRAW` (AC 1.1) — يُتحكّم بإظهاره عبر نفس منطق تبديل الوضع الموجود.
- أثناء التسجيل: مؤشر مرئي `#voice-recording-indicator` (نبضة حمراء) (AC 1.2، 1.3).
- عند رفض إذن الميكروفون أو غياب جهاز: `catch` على `getUserMedia` → رسالة خطأ وصفية عبر `pushFeed`/`setNotice` الموجودة (AC 1.8).
- إذا أرجع الـ endpoint نصاً فارغاً → رسالة "no speech recognized" دون تغيير الوضع (AC 1.7).

### المكوّن 2: VoiceCommandRouter (Frontend — JS)

يصنّف النص المُتعرَّف عليه ويوجّهه.

```javascript
const VoiceCommandRouter = {
  // قواعد المطابقة (case-insensitive, trimmed)
  // "text mode" → switch text ; "draw mode" → switch draw
  // "draw picture number N" → draw library (draw mode only)
  // غير ذلك في text mode → نص حرفي للكتابة
  route(transcript) {
    const norm = transcript.trim().toLowerCase();
    if (norm === 'text mode')  return this.requestMode('text');
    if (norm === 'draw mode')  return this.requestMode('draw');
    const drawMatch = norm.match(/^draw\s+picture\s+number\s+(\d+)$/);
    if (drawMatch && activeMode === 'draw') return this.drawLibrary(Number(drawMatch[1]));
    if (activeMode === 'text') return TextDictationController.present(transcript); // verbatim, NOT lowercased
    // draw mode + غير مطابق → لا إجراء (AC 2.6 / 4.5 message)
  },
  async requestMode(mode),   // setMode(mode) الموجودة → POST /api/mode
  async drawLibrary(n),      // POST /api/draw_library/{n}
};
```

ملاحظات دقّة:
- مطابقة الأوامر تستخدم النص **منخفض الأحرف والمشذّب** فقط لـ **القرار**؛ أما النص المعروض للكتابة فيُحفظ **حرفياً كما نُطق** (AC 1.5، 3.1، 3.7).
- grammar الرسم: `^draw\s+picture\s+number\s+(\d+)$` (AC 4.2).

### المكوّن 3: TextDictationController (Frontend — JS)

يدير عرض النص المُملى ونافذة الإلغاء.

```javascript
const TextDictationController = {
  state: { pendingText: null, timerId: null, secondsLeft: 0 },

  present(transcript) {
    this.cancelPending();                 // أي معلّق سابق يُلغى (AC 3.6)
    this.state.pendingText = transcript;  // حرفي
    renderTranscript(transcript);         // #voice-transcript (clickable)
    this.startCountdown(5);               // #voice-countdown 5→0
  },
  startCountdown(seconds),                // setInterval 1s, يحدّث العرض
  cancelPending(),                        // clearInterval, يبقي النص ظاهراً وقابلاً للتحرير (AC 3.4, 3.5)
  async commit() {                        // عند بلوغ 0
    // إجراء واحد: preview ثم draw
    await SingleActionText.run(this.state.pendingText);
  },
};
```

- `#voice-transcript` له `onclick = cancelPending` (AC 3.4).
- الإلغاء **لا** يمحو النص؛ يبقى في `#text-input` للتحرير/إعادة التسجيل (AC 3.5).
- وصول transcript جديد أثناء العدّ → `present()` يُلغي ويعيد البدء (AC 3.6).

### المكوّن 4: SingleActionText / SingleActionSketch (Frontend — JS)

يستبدل خطوتي «Generate Preview ثم Draw» بإجراء واحد.

```javascript
const SingleActionText = {
  async run(text) {
    // 1) preview
    const preview = await apiRequest('/api/preview', { ... input_type:'text', text ... });
    state.currentPreviewId = preview.preview_id;
    state.previewDirty = false;
    // 2) draw فوراً بنفس preview_id
    await ensureMode('text');
    await apiRequest('/api/draw', { preview_id: state.currentPreviewId });
  }
};
```

- **زر النص**: يُحذف `#text-preview-btn`؛ يبقى زر واحد `#text-submit-btn` تُعاد تسميته (مثلاً "Write") ويستدعي `SingleActionText.run()` مباشرةً (AC 5.1، 5.2، 5.3).
- إذا فشل أحد النداءين → رسالة خطأ وصفية، والوضع لا يتغيّر (AC 5.5).
- نفس النمط يُطبّق على لوحة الملف (زر واحد للرسم).

### المكوّن 5: voice_transcribe (Backend — ملف جديد `wall_climber/voice_transcribe.py`)

```python
def transcribe_audio(audio_bytes: bytes, content_type: str) -> dict:
    """
    يحوّل صوت إنجليزي منطوق إلى نص باستخدام faster-whisper.
    Returns: {'text': str, 'engine': 'faster_whisper', 'language': 'en'}
    Raises: TranscriptionUnavailable (إذا النموذج غير متاح) → الـ endpoint يرجّع 503
    """
```

- يحمّل نموذج `faster_whisper.WhisperModel('base.en', device='cpu', compute_type='int8')` مرة واحدة (lazy singleton).
- مقيّد بـ `language='en'` لرفع دقة الإنجليزية (AC 1.6).
- يحوّل WebM/opus → WAV عبر `ffmpeg`/`av` إذا لزم؛ أو يمرّر مباشرة إذا الصيغة مدعومة.
- إذا الاستيراد فشل (`ImportError`) أو تحميل النموذج فشل → يرفع `TranscriptionUnavailable` → الـ endpoint 503 → الواجهة تسقط للـ Web Speech API.

نقطة النهاية في `web_server.py`:

```python
@app.post('/api/voice/transcribe')
async def voice_transcribe(file: UploadFile = File(...)) -> JSONResponse:
    content = await file.read(_MAX_AUDIO_BYTES + 1)   # حدّ حجم جديد ~10MB
    # validate content-type ∈ {audio/webm, audio/wav, audio/ogg, audio/mp4}
    try:
        result = voice_transcribe.transcribe_audio(content, file.content_type)
    except TranscriptionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return JSONResponse({'ok': True, 'text': result['text'], 'engine': result['engine']})
```

### المكوّن 6: draw_library (Backend — ملف جديد `wall_climber/draw_library.py`)

```python
class DrawLibrary:
    def __init__(self, library_dir: Path):  # share/wall_climber/assets/draw_library
        ...
    def resolve(self, identifier: int) -> DrawLibraryEntry | None:
        """يقرأ manifest.json ويرجّع المدخل المطابق للـ id، أو None."""
    def load_image_bytes(self, entry) -> bytes:
        """يقرأ ملف الصورة؛ يرفع FileNotFoundError إذا مفقود."""
```

`DrawLibraryEntry`: `{id:int, name:str, file:str, default_mode:str, description:str}` (نفس شكل `manifest.example.json`).

نقطة النهاية:

```python
@app.post('/api/draw_library/{identifier}')
async def draw_from_library(identifier: int) -> JSONResponse:
    entry = draw_library.resolve(identifier)
    if entry is None:
        raise HTTPException(status_code=404, detail=f'picture number {identifier} was not found')
    try:
        image_bytes = draw_library.load_image_bytes(entry)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail=f'image file for picture {identifier} is missing: {exc}')
    # نفس مسار رفع الصورة: vectorize → preview_id → draw
    preview = await _preview_from_image_bytes(image_bytes, filename=entry.file, settings={})
    entry_cache = _load_preview(preview['preview_id'])
    return JSONResponse(_draw_cached_preview_response(entry_cache))
```

- يستخدم **نفس** مسار `preview_sketch_centerline` ثم `draw_cached_preview` (AC 4.3، 4.4) — لا منطق رسم جديد.
- المكتبة في `assets/draw_library/` وتُثبّت عبر `setup.py` (موجود فعلاً: `package_files('assets', ...)`). نضيف `manifest.json` فعلي (إلى جانب `manifest.example.json`) ومجلد `examples/` للصور (AC 4.1).
- رسالة "picture number N was not found" عند غياب المدخل (AC 4.5)، ورسالة ملف مفقود عند غياب الملف (AC 4.6).
- يرث رفض 409 عند الانشغال من `publish_execution_plan` (AC 4.7).

### المكوّن 7: Thin Line Filter (Backend — ملف جديد `image_pipeline/_thin_line_filter.py`)

هذا المكوّن يحلّ القيد الأساسي: **القلم له عرض ثابت (`pen.tip_radius = 0.003 m` ≈ 6 mm سماكة)**، فالخطوط الأرفع من ذلك تبدو غريبة عند الرسم.

```python
def filter_thin_lines(
    binary_mask: numpy.ndarray,      # القناع الثنائي قبل التنحيف
    *,
    min_stroke_width_px: float,      # عتبة مشتقّة من قيمة السلايدر
) -> tuple[numpy.ndarray, dict]:
    """
    يزيل المناطق التي عرضها المحلي < min_stroke_width_px باستخدام
    distance transform. كل بكسل قيمته في distance transform = نصف
    العرض المحلي للسكتة؛ نُبقي فقط البكسلات التي 2*dist >= العتبة،
    ثم نعيد بناء السكتات السميكة كافية عبر morphological reconstruction.
    """
    dist = cv2.distanceTransform(binary_mask, cv2.DIST_L2, 5)
    # نواة السكتة = البكسلات السميكة كفاية (مركز السكتات العريضة)
    core = (2.0 * dist >= min_stroke_width_px).astype(uint8)
    # إعادة البناء: نُبقي أي مكوّن متّصل يحتوي على بكسل core واحد على الأقل
    kept = _reconstruct_components_containing(binary_mask, core)
    return kept, {'thin_line_min_width_px': min_stroke_width_px, ...}
```

الخوارزمية:
1. `cv2.distanceTransform` على القناع الثنائي: قيمة كل بكسل = المسافة لأقرب خلفية = نصف العرض المحلي.
2. سكتة عرضها `w` بكسل لها قيمة قصوى ≈ `w/2` في الـ distance transform.
3. نحتفظ فقط بالمكوّنات المتّصلة التي تحوي بكسلاً واحداً على الأقل بـ `2*dist >= min_stroke_width_px` — أي السكتات التي تبلغ العتبة في مكان ما. هذا يحذف الخطوط الشعرية بالكامل بدل تقطيعها.

ربط العتبة بعرض القلم (AC 8.4):
```
pen_stroke_width_px = (2 * pen.tip_radius) / scale_m_per_px
min_stroke_width_px = slider_fraction * pen_stroke_width_px
```
حيث `slider_fraction ∈ [0, 1]`: 0 = تعطيل الفلتر (يبقي كل شي)، 1 = يحذف كل خط أرفع من عرض القلم الكامل.

نقطة الإدخال في الـ pipeline: يُستدعى داخل `vectorize_sketch_image_to_plan` **بعد** `_remove_small_components` و**قبل** `_skeletonize_foreground`:

```python
# داخل vectorize_sketch_image_to_plan، معامل جديد:
#   thin_line_min_width_mm: float = 0.0   (0 = معطّل)
if thin_line_min_width_mm > 0.0:
    min_width_px = (thin_line_min_width_mm / 1000.0) / scale_m_per_px
    cleaned_binary, thin_meta = filter_thin_lines(cleaned_binary, min_stroke_width_px=min_width_px)
```

> ملاحظة: نمرّر العتبة بالمليمتر (`thin_line_min_width_mm`) بدل الكسر، لأنها أوضح للمستخدم وأكثر استقراراً عبر دقّات المعالجة المختلفة. الواجهة تعرض mm، والـ backend يحوّل لبكسل عبر `scale_m_per_px`.

### المكوّن 8: Face Region Handling (Backend — ملف جديد `image_pipeline/_face_regions.py`)

```python
def detect_face_regions(gray: numpy.ndarray) -> list[tuple[int,int,int,int]]:
    """
    يكشف مناطق الوجه (boxes). يستخدم cv2 Haar cascade المدمج
    (cv2.data.haarcascades + 'haarcascade_frontalface_default.xml').
    Returns: قائمة boxes [(x,y,w,h)], فارغة إذا لا وجه.
    """

def apply_face_preserving_threshold(
    binary: numpy.ndarray,
    gray: numpy.ndarray,
    face_boxes: list,
    *,
    line_sensitivity: float,
) -> numpy.ndarray:
    """
    داخل كل face box، يعيد العتبة بمعاملات ألطف تحفظ ملامح
    العينين/الفم كخطوط مفتوحة بدل كتل مملوءة:
      - block_size أصغر للـ adaptive threshold (تفاصيل أدقّ)
      - يمنع ملء المناطق المغلقة الصغيرة (العين) عبر إبقاء الحواف فقط
    يدمج النتيجة فوق القناع العام داخل الـ box فقط.
    """
```

المنطق:
- لماذا تتشوّه الوجوه: العين/الفم مناطق صغيرة عالية التباين؛ العتبة العامة (adaptive block كبير) تملأها ككتل، والتنحيف يحوّلها لنقطة/شرطة. الحل: داخل صندوق الوجه نستخدم عتبة أدقّ + كشف حواف يحفظ الخطوط المفتوحة.
- التكامل: يُستدعى `detect_face_regions` بعد التحويل لرمادي، و`apply_face_preserving_threshold` بعد `_threshold_foreground`، يعدّل فقط بكسلات داخل صناديق الوجه (AC 7.2، 7.3، 7.6). إذا لا وجه → لا تغيير (AC 7.5).
- معامل جديد في الـ pipeline: `enable_face_handling: bool = True` (يُشتقّ من إعداد واجهة، افتراضياً مفعّل).

> اعتمادية: Haar cascade مدمج مع OpenCV (`cv2.data.haarcascades`) — لا تنزيل خارجي. إذا لم يتوفّر `cv2.data`، نتعامل معها كـ "لا وجوه مكتشفة" ونكمل عادي (AC 7.5).

### تنظيف إعدادات السكتش (Requirement 6)

بناءً على فحص `readFilePreviewSettings()` و`preview_sketch_centerline`:

| الإعداد | element id | القرار | الافتراضي المُطبّق |
|---------|-----------|--------|---------------------|
| Processing Resolution | `#sketch-resolution` | **يبقى** + شرح | 1000 px |
| Path Smoothness | `#sketch-preview-geometry` | **يبقى** + شرح | smooth_curves |
| Thin Line Filter | (جديد) `#sketch-thin-line` | **يُضاف** | 0 (معطّل) |
| Curve Tolerance | `#sketch-curve-tolerance` | يُخفى | 0.6 |
| Margin (m) | `#sketch-margin` | يُخفى | 0.05 |
| Noise Area | `#sketch-min-component` | يُخفى | 2 |
| Line Sensitivity | `#sketch-line-sensitivity` | يُخفى | 0.35 |
| Min Stroke Length | `#sketch-min-stroke` | يُخفى | 1.0 |
| Skeleton Prune | `#sketch-skeleton-prune` | يُخفى | 6.0 |
| Path Optimizer | `#path-optimizer` | يُخفى | internal |
| Stroke Merge Gap | `#sketch-merge-gap` | يُخفى | 0.0 |
| Simplify Epsilon | `#sketch-simplify-epsilon` | يُخفى | 0.25 |
| Tiny Detect Max | `#sketch-tiny-candidate-mm` | يُخفى | 3.4 mm |
| Sketch Scale | `#sketch-scale-percent` | يُخفى | 100 |
| Sketch Center X/Y | `#sketch-center-x/y` | يُخفى | auto |

طريقة الإخفاء: الـ backend `preview_sketch_centerline` **يطبّق الافتراضيات أصلاً** عندما تكون القيمة `None`. لذلك التنظيف في الواجهة فقط: نحذف/نخفي عناصر `Advanced sketch settings` من DOM ونتأكّد أن `readFilePreviewSettings()` لا يرسلها (أو يرسلها بقيمها الافتراضية الثابتة). الـ backend يبقى متوافقاً مع الطلبات القديمة (AC 6.4–6.7). نضيف نصوص شرح قصيرة للعناصر المُبقاة (AC 6.3).

## Data Models

### نموذج طلب/استجابة Voice Transcribe

```
POST /api/voice/transcribe   (multipart/form-data)
  file: audio blob (audio/webm | audio/wav | audio/ogg | audio/mp4)
→ 200 { ok: true, text: "<english transcript>", engine: "faster_whisper" }
→ 503 { detail: "transcription engine unavailable" }   # الواجهة تسقط للـ Web Speech API
→ 422 { detail: "audio content-type must be one of ..." }
```

### نموذج Draw Library Manifest (موجود، نلتزم به)

```json
{
  "version": 1,
  "entries": [
    { "id": 1, "name": "...", "file": "examples/x.png",
      "default_mode": "sketch_centerline", "description": "..." }
  ]
}
```

```
POST /api/draw_library/{identifier:int}
→ 200 (same shape as /api/draw response: ok, published, preview_id, ...)
→ 404 { detail: "picture number N was not found" }          # لا مدخل
→ 404 { detail: "image file for picture N is missing: ..." } # ملف مفقود
→ 409 (موروث من publish_execution_plan عند الانشغال)
```

### معاملات الـ pipeline الجديدة

```python
vectorize_sketch_image_to_plan(
    ...,
    thin_line_min_width_mm: float = 0.0,   # 0 = معطّل ؛ >0 يفعّل filter_thin_lines
    enable_face_handling: bool = True,     # كشف ومعالجة مناطق الوجه
)
```

ويُمرَّران من `preview_sketch_centerline` كـ `Form` params جديدين:
- `thin_line_min_width_mm: Optional[float] = Form(None)` (افتراضي 0.0، مدى 0.0–6.0)
- `enable_face_handling: Optional[bool] = Form(None)` (افتراضي True)

عناصر الواجهة الجديدة:
- سلايدر فلتر الخطوط `#sketch-thin-line` (range): min=0، max=6 (mm)، step=0.5، value=0 (معطّل افتراضياً، AC 8.2)، مع readout `#sketch-thin-line-readout` (AC 8.5).

## Error Handling

| الحالة | السلوك | المتطلب |
|--------|--------|----------|
| رفض إذن الميكروفون / لا جهاز | رسالة خطأ وصفية، لا تسجيل | AC 1.8 |
| transcription فارغ | "no speech recognized"، الوضع لا يتغيّر | AC 1.7 |
| الـ backend transcribe غير متاح (503) | fallback تلقائي للـ Web Speech API؛ إذا هو أيضاً غير مدعوم → رسالة واضحة | قرار التصميم |
| تبديل وضع صوتي أثناء الانشغال | 409 موروث، رسالة "runtime is busy" | AC 2.5 |
| رسم من المكتبة برقم غير موجود | 404 "picture number N was not found" | AC 4.5 |
| ملف صورة المكتبة مفقود | 404 رسالة ملف مفقود | AC 4.6 |
| فشل الكتابة بعد الإجراء الواحد | رسالة خطأ، الوضع لا يتغيّر | AC 5.5 |
| الفلتر يحذف كل السكتات | تحذير "current filter leaves nothing to draw" | AC 8.7 |
| لا وجه مكتشف | معالجة عادية بدون منطق وجه | AC 7.5 |
| Haar cascade غير متاح | يُعامَل كـ "لا وجوه"، يكمل عادي | AC 7.5 |

نمط معالجة الأخطاء يتبع الموجود: `HTTPException` في الـ backend، و`pushFeed`/`setNotice`/`try-catch` في الواجهة.

## Testing Strategy

ملفات الاختبار الحالية ذات الصلة (تحت `src/wall_climber/test/`):
- `test_sketch_centerline_pipeline.py`، `test_sketch_pipeline_phase3_integration.py`، `test_sketch_centerline_curve_fit.py`.
- اختبارات الـ endpoints (`test_sketch_centerline_preview_endpoint.py`, `test_sketch_centerline_draw_endpoint.py`, `test_preview_cache_contract.py`) تعتمد `httpx`/`TestClient` وتُستثنى في بيئة التشغيل الحالية.

أمر الاختبار المعتمد:
```
PYTHONPATH=src/wall_climber python3 -m pytest -q src/wall_climber/test \
  --ignore=.../test_preview_cache_contract.py \
  --ignore=.../test_sketch_centerline_draw_endpoint.py \
  --ignore=.../test_sketch_centerline_preview_endpoint.py \
  -p no:anyio --rootdir=src/wall_climber
```

اختبارات جديدة:

| الوحدة | ملف الاختبار | يغطّي |
|--------|--------------|--------|
| `filter_thin_lines` | `test_thin_line_filter.py` | حذف خط شعري، إبقاء خط سميك، عتبة 0 = بلا تغيير، حذف الكل → قناع فارغ (R8) |
| `detect_face_regions` / face threshold | `test_face_regions.py` | كشف وجه في صورة اختبار، إبقاء ملامح مفتوحة، لا وجه = بلا تغيير، عدم تدهور غير الوجه (R7) |
| `DrawLibrary.resolve/load` | `test_draw_library.py` | مطابقة id، id مفقود → None، ملف مفقود → استثناء، قراءة manifest (R4) |
| `voice_transcribe` | `test_voice_transcribe.py` | `TranscriptionUnavailable` عند غياب النموذج (mock)؛ تطبيع content-type (R1) |
| `vectorize_sketch_image_to_plan` (موسّع) | تمديد `test_sketch_centerline_pipeline.py` | تمرير `thin_line_min_width_mm` و`enable_face_handling` لا يكسر المسار الافتراضي |

اختبارات الـ endpoints الجديدة (`/api/voice/transcribe`, `/api/draw_library/{id}`) تُضاف للملفات المعتمدة على `httpx` (تتبع نمط الملفات المستثناة) لتعمل في بيئات الـ CI التي فيها httpx.

اختبارات الواجهة (VoiceController, TextDictationController, single-action) يدوية عبر سيناريوهات قبول موصوفة في `tasks.md`، لعدم وجود إطار اختبار JS في المشروع حالياً.

التحقّق بعد كل تغيير: `colcon build --packages-select wall_climber` ثم تشغيل مجموعة الاختبارات، وأخيراً تحقّق `xacro` ليس مطلوباً هنا (لا تغييرات URDF).

## Correctness Properties

هذه الخصائص ثابتة (invariants) يجب أن يحافظ عليها التنفيذ، وكل واحدة قابلة للاختبار:

### Property 1: Verbatim transcript
النص المعروض/المكتوب يطابق مخرجات `Speech_Transcriber` حرفياً (لا تحويل حالة، لا ترجمة، لا إعادة صياغة). المطابقة لتصنيف الأوامر تستخدم نسخة منخفضة-الأحرف منفصلة عن النص المحفوظ.

**Validates: Requirements 1.5, 3.1, 3.7**

### Property 2: Cancel-window safety
إذا نُقر على النص خلال نافذة الإلغاء أو وصل transcript جديد، فإن أي كتابة معلّقة سابقة **لا** تُرسل أبداً للروبوت. لا يوجد مسار يكتب نصاً مُلغى.

**Validates: Requirements 3.4, 3.6**

### Property 3: Single source of truth للوضع
كل تبديل وضع (صوتي أو يدوي) يمرّ حصراً عبر `switch_mode()` → `ACTIVE_MODE_TOPIC`؛ لا يوجد مسار يضبط الوضع متجاوزاً فحص الانشغال (409) أو الجاهزية (503).

**Validates: Requirements 2.3, 2.5**

### Property 4: Reuse-only execution
كل رسم/كتابة (صوتي، مكتبة، إجراء واحد) ينتهي عند `publish_execution_plan` عبر `preview_id` مُخزّن؛ لا يوجد مسار نشر بديل لـ `PRIMITIVE_PATH_PLAN_TOPIC`.

**Validates: Requirements 4.4, 5.3**

### Property 5: Filter monotonicity & validity
زيادة قيمة `Thin_Line_Filter` لا تزيد أبداً عدد السكتات المُبقاة (دالة غير-تصاعدية)؛ عند القيمة الدنيا (0) تبقى كل السكتات غير-صفرية العرض، وتُستبعد دائماً السكتات صفرية العرض.

**Validates: Requirements 8.3, 8.6**

### Property 6: Default-equivalence للإعدادات المخفية
لأي صورة وإعدادات مُبقاة، إخفاء الإعدادات المتقدمة ينتج معاينة مطابقة لطلب بالقيم الافتراضية الصريحة (لا انحراف سلوكي عند الإخفاء).

**Validates: Requirements 6.4, 6.5, 6.6, 6.7**

### Property 7: Non-face preservation
تفعيل معالجة الوجه لا يغيّر بكسلات خارج صناديق الوجه المكتشفة؛ المناطق غير-الوجه تنتج نفس المسارات كما بدون معالجة الوجه.

**Validates: Requirements 7.6**

### Property 8: Graceful degradation
غياب أي اعتمادية اختيارية (نموذج التفريغ الصوتي، Haar cascade، ffmpeg) لا يكسر النظام: التفريغ يسقط للـ Web Speech API، وكشف الوجه يُعامَل كـ "لا وجوه"، وفكّ الصوت يسقط لـ WAV.

**Validates: Requirements 1.7, 7.5**

## Open Design Decisions / Risks

1. **حجم نموذج faster-whisper**: `base.en` (~140MB) متوازن للـ CPU؛ `small.en` (~460MB) أدقّ وأبطأ. التصميم يبدأ بـ `base.en` ويسمح بالتهيئة عبر متغيّر بيئة. خطر: زمن التنزيل الأول وحجم القرص داخل الـ devcontainer.
2. **صيغة الصوت من المتصفح**: `MediaRecorder` ينتج `audio/webm;codecs=opus` في Chromium. `faster-whisper` يحتاج فكّ ترميز (عبر `av`/`ffmpeg`). إذا `ffmpeg` غير متاح، نرسل WAV من الواجهة عبر `AudioContext` بدل WebM (بديل احتياطي).
3. **دقّة كشف الوجه بـ Haar**: Haar cascades سريعة لكن قد تخطئ في الرسوم الكرتونية/الخطّية (مش صور فوتوغرافية). إذا فشل الكشف على الرسوم، البديل: السماح للمستخدم بتحديد منطقة الوجه يدوياً (مرحلة لاحقة، خارج النطاق الحالي). للصور الفوتوغرافية والبورتريهات الواقعية يعمل جيداً.
4. **الفلتر قبل أم بعد المعالجة المسبقة**: نضعه بعد `_remove_small_components` مباشرةً على القناع الثنائي، حيث معلومات العرض ما زالت موجودة وقبل أن يهدمها التنحيف.
