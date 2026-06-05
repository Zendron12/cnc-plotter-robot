# Implementation Plan

## Overview

خطة تنفيذ تدريجية لميزة `voice-control-and-drawing-ux`. المهام مرتّبة من الأسفل للأعلى: وحدات الـ pipeline المعزولة أولاً (قابلة للاختبار وحدها)، ثم نقاط نهاية الـ backend، ثم ربط الواجهة، وأخيراً التغليف والتحقّق. كل مهمة تُبنى على ما قبلها ولا تترك كوداً يتيماً.

أمر الاختبار المعتمد بعد كل مهمة backend:
```
PYTHONPATH=src/wall_climber python3 -m pytest -q src/wall_climber/test \
  --ignore=src/wall_climber/test/test_preview_cache_contract.py \
  --ignore=src/wall_climber/test/test_sketch_centerline_draw_endpoint.py \
  --ignore=src/wall_climber/test/test_sketch_centerline_preview_endpoint.py \
  -p no:anyio --rootdir=src/wall_climber
```
والبناء: `bash -lc "source /opt/ros/humble/setup.bash && colcon build --packages-select wall_climber"`

## Tasks

### 1. تحسينات جودة الرسم (Sketch Pipeline)

- [x] 1. أنشئ وحدة فلتر الخطوط الرفيعة `_thin_line_filter.py`
  - أنشئ `src/wall_climber/wall_climber/image_pipeline/_thin_line_filter.py` يحوي `filter_thin_lines(binary_mask, *, min_stroke_width_px) -> tuple[ndarray, dict]`.
  - استخدم `cv2.distanceTransform(binary_mask, cv2.DIST_L2, 5)`؛ احسب نواة `core = (2*dist >= min_stroke_width_px)`؛ ثم أبقِ فقط المكوّنات المتّصلة (`cv2.connectedComponentsWithStats`, connectivity=8) التي تحوي بكسل `core` واحداً على الأقل.
  - أرجع القناع المُصفّى + ميتاداتا `{thin_line_min_width_px, components_total, components_kept, components_removed}`.
  - احرص أن `min_stroke_width_px <= 0` يرجّع القناع كما هو (تعطيل)، وأن السكتات صفرية العرض دائماً مستبعَدة.
  - _Requirements: 8.3, 8.4, 8.6_
  - _Design: Component 7 (Thin Line Filter), Property 5_

- [x] 2. اكتب اختبارات وحدة فلتر الخطوط الرفيعة
  - أنشئ `src/wall_climber/test/test_thin_line_filter.py`.
  - حالات: (أ) قناع فيه خط شعري (1px) وخط سميك (8px) → الفلتر بعتبة بين الاثنين يبقي السميك ويحذف الشعري؛ (ب) عتبة 0 → القناع بلا تغيير؛ (ج) عتبة أكبر من كل السكتات → قناع فارغ؛ (د) خاصية الرتابة: زيادة العتبة لا تزيد عدد البكسلات المُبقاة.
  - استخدم `numpy` لبناء أقنعة اصطناعية (بلا اعتماد على ملفات).
  - _Requirements: 8.3, 8.6_
  - _Design: Testing Strategy, Property 5_

- [x] 3. ادمج فلتر الخطوط الرفيعة في `vectorize_sketch_image_to_plan`
  - في `src/wall_climber/wall_climber/image_pipeline/sketch_centerline.py` أضف المعامل `thin_line_min_width_mm: float = 0.0` لتوقيع `vectorize_sketch_image_to_plan`.
  - بعد `_remove_small_components` وقبل `_skeletonize_foreground`: إذا `thin_line_min_width_mm > 0`، احسب `min_width_px = (thin_line_min_width_mm/1000.0) / scale_m_per_px` ثم استدعِ `filter_thin_lines`، وأضف الميتاداتا للـ plan.
  - تأكّد من توفّر `scale_m_per_px` في هذه المرحلة (اشتقّه من نفس مصدر الـ metadata الحالي)؛ إذا غير متاح بعد، احسبه من أبعاد المعالجة.
  - أضف للـ `test_sketch_centerline_pipeline.py` حالة تتأكّد أن `thin_line_min_width_mm=0.0` (الافتراضي) لا يغيّر المخرجات مقابل المسار الحالي.
  - _Requirements: 8.3, 8.4, 8.6, 8.7_
  - _Design: Component 7, Data Models (pipeline params)_

- [x] 4. أنشئ وحدة معالجة مناطق الوجه `_face_regions.py`
  - أنشئ `src/wall_climber/wall_climber/image_pipeline/_face_regions.py` يحوي `detect_face_regions(gray) -> list[(x,y,w,h)]` و`apply_face_preserving_threshold(binary, gray, face_boxes, *, line_sensitivity) -> ndarray`.
  - `detect_face_regions`: حمّل Haar cascade من `cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'`؛ إن لم يتوفّر `cv2.data` أو فشل التحميل، أرجع `[]` (لا استثناء).
  - `apply_face_preserving_threshold`: داخل كل صندوق وجه فقط، أعد العتبة بـ `adaptiveThreshold` بـ block_size أصغر لتفاصيل أدقّ، وادمج النتيجة فوق القناع العام داخل الصندوق حصراً (لا تلمس بكسلات خارجه).
  - _Requirements: 7.1, 7.2, 7.3, 7.5_
  - _Design: Component 8 (Face Region Handling), Property 7_

- [x] 5. اكتب اختبارات وحدة مناطق الوجه
  - أنشئ `src/wall_climber/test/test_face_regions.py`.
  - حالات: (أ) `detect_face_regions` على صورة بلا وجه (تدرّج رمادي بسيط) → `[]`؛ (ب) صندوق وجه اصطناعي → `apply_face_preserving_threshold` يعدّل فقط البكسلات داخل الصندوق ويترك الخارج مطابقاً بالضبط (خاصية عدم تدهور غير-الوجه)؛ (ج) غياب `cv2.data` (محاكاة عبر monkeypatch) → `[]` بدون استثناء.
  - _Requirements: 7.5, 7.6_
  - _Design: Testing Strategy, Property 7_

- [x] 6. ادمج معالجة الوجه في `vectorize_sketch_image_to_plan`
  - أضف المعامل `enable_face_handling: bool = True` للتوقيع.
  - بعد التحويل لرمادي استدعِ `detect_face_regions`؛ بعد `_threshold_foreground` وإذا وُجدت صناديق وجه و`enable_face_handling`، طبّق `apply_face_preserving_threshold` على القناع.
  - سجّل في الـ plan metadata عدد الوجوه المكتشفة. إذا لا وجوه → لا تغيير في السلوك (المسار العادي).
  - أضف حالة اختبار: `enable_face_handling=True` على صورة بلا وجه ينتج نفس مخرجات `enable_face_handling=False`.
  - _Requirements: 7.1, 7.2, 7.5, 7.6_
  - _Design: Component 8_

### 2. مكتبة الرسم (Draw Library Backend)

- [x] 7. أنشئ خدمة `DrawLibrary`
  - أنشئ `src/wall_climber/wall_climber/draw_library.py` يحوي `DrawLibraryEntry` (dataclass: id, name, file, default_mode, description) و`class DrawLibrary`.
  - `DrawLibrary(library_dir)`: يقرأ `manifest.json` (أو `manifest.example.json` كاحتياطي إذا غاب الفعلي)؛ يتعامل مع JSON تالف بإرجاع قائمة فارغة دون انهيار.
  - `resolve(identifier: int) -> DrawLibraryEntry | None`؛ `load_image_bytes(entry) -> bytes` (يرفع `FileNotFoundError` إذا الملف مفقود، ويحمي من path traversal بحصر الملف داخل `library_dir`).
  - _Requirements: 4.1, 4.2, 4.5, 4.6_
  - _Design: Component 6 (draw_library)_

- [x] 8. اكتب اختبارات `DrawLibrary`
  - أنشئ `src/wall_climber/test/test_draw_library.py` مع مجلد مؤقت + manifest اصطناعي.
  - حالات: مطابقة id موجود؛ id مفقود → `None`؛ مدخل يشير لملف مفقود → `load_image_bytes` يرفع `FileNotFoundError`؛ manifest تالف → قائمة فارغة؛ محاولة traversal في حقل `file` تُرفض.
  - _Requirements: 4.1, 4.5, 4.6_
  - _Design: Testing Strategy_

- [x] 9. أضف ملف `manifest.json` فعلي ومجلد أمثلة للمكتبة
  - أنشئ `assets/draw_library/manifest.json` (نسخة عاملة من `manifest.example.json`) ومجلد `assets/draw_library/examples/` مع صورة sketch مثال واحدة على الأقل (PNG خطّي بسيط) ليكون "draw picture number 1" قابلاً للتجربة فوراً.
  - تأكّد أن `setup.py` يثبّت `assets/` (موجود فعلاً عبر `package_files('assets', ...)`)؛ أعد البناء وتحقّق من وجود الملفات في `install/wall_climber/share/wall_climber/assets/draw_library/`.
  - _Requirements: 4.1_
  - _Design: Component 6, Overview (compat note)_

- [x] 10. أضف نقطة النهاية `/api/draw_library/{identifier}`
  - في `web_server.py`: أنشئ instance من `DrawLibrary` في `create_app`/`BackendRuntime` يشير لـ `get_package_share_directory('wall_climber')/'assets'/'draw_library'`.
  - أضف `@app.post('/api/draw_library/{identifier}')`: `resolve` → 404 "picture number N was not found" إذا None؛ `load_image_bytes` → 404 برسالة ملف مفقود عند الخطأ؛ ثم مرّر البايتات عبر **نفس** مسار `preview_sketch_centerline` (استخرج دالة مساعدة `_preview_from_image_bytes` تُعيد استخدام منطق المعاينة الحالي دون تكرار)، ثم `_load_preview` و`_draw_cached_preview_response`.
  - الانشغال (409) يُورَّث تلقائياً من `publish_execution_plan`.
  - _Requirements: 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_
  - _Design: Component 6, Property 4_

- [x] 11. اكتب اختبار endpoint مكتبة الرسم
  - أضف لملف معتمد على `httpx`/`TestClient` (نمط `test_sketch_centerline_draw_endpoint.py`) اختبارات: رقم موجود → 200 ونشر خطة؛ رقم مفقود → 404 بالرسالة الصحيحة؛ ملف مفقود → 404؛ والانشغال → 409 (عبر fake node زي الموجود في اختبارات draw الحالية).
  - _Requirements: 4.4, 4.5, 4.6, 4.7_
  - _Design: Testing Strategy_

### 3. التعرّف على الصوت (Voice Transcription Backend)

- [x] 12. أنشئ وحدة `voice_transcribe`
  - أنشئ `src/wall_climber/wall_climber/voice_transcribe.py` يحوي `class TranscriptionUnavailable(Exception)` و`transcribe_audio(audio_bytes, content_type) -> dict`.
  - حمّل `faster_whisper.WhisperModel` كـ lazy singleton (نموذج `base.en`، `device='cpu'`, `compute_type='int8'`)، مع قراءة اسم النموذج من متغيّر بيئة `WALL_CLIMBER_WHISPER_MODEL` (افتراضي `base.en`).
  - فكّ ترميز WebM/opus → WAV عبر `av`/`ffmpeg` إذا لزم؛ قيّد التفريغ بـ `language='en'`. أرجع `{text, engine:'faster_whisper', language:'en'}`.
  - أي `ImportError`/فشل تحميل/فشل فكّ ترميز → ارفع `TranscriptionUnavailable` برسالة وصفية.
  - _Requirements: 1.4, 1.6, 1.9_
  - _Design: Component 5 (voice_transcribe), Property 8_

- [x] 13. اكتب اختبارات `voice_transcribe`
  - أنشئ `src/wall_climber/test/test_voice_transcribe.py`.
  - عبر monkeypatch: محاكاة غياب `faster_whisper` (ImportError) → `transcribe_audio` يرفع `TranscriptionUnavailable`؛ والتحقّق من تطبيع/رفض content-type غير المدعوم.
  - لا تنزّل نموذجاً فعلياً في الاختبار (mock للـ WhisperModel).
  - _Requirements: 1.9_
  - _Design: Testing Strategy, Property 8_

- [x] 14. أضف نقطة النهاية `/api/voice/transcribe`
  - في `web_server.py`: أضف ثابت `_MAX_AUDIO_BYTES` (~10MB) ومجموعة content-types مسموحة `{audio/webm, audio/wav, audio/ogg, audio/mp4}`.
  - `@app.post('/api/voice/transcribe')` يقرأ الملف، يتحقّق من النوع (422 إن غير مدعوم)، يستدعي `voice_transcribe.transcribe_audio`؛ عند `TranscriptionUnavailable` → 503؛ النجاح → `{ok, text, engine}`.
  - _Requirements: 1.4, 1.9_
  - _Design: Component 5, Data Models (transcribe)_

- [x] 15. اكتب اختبار endpoint التفريغ الصوتي
  - أضف لملف معتمد على `httpx`/`TestClient`: محاكاة `transcribe_audio` يرجّع نصاً → 200؛ ومحاكاته يرفع `TranscriptionUnavailable` → 503؛ ونوع محتوى غير مدعوم → 422.
  - _Requirements: 1.7, 1.9_
  - _Design: Testing Strategy_

- [x] 16. أضف `faster-whisper` لاعتماديات الحزمة
  - أضف `faster-whisper` لـ `install_requires` في `setup.py` (مع ملاحظة أنها اختيارية سلوكياً: الـ endpoint يرجّع 503 إن غابت).
  - وثّق أن أوزان النموذج تُنزَّل مرة واحدة عند أول استخدام وتُخزَّن في كاش المستخدم.
  - _Requirements: 1.9_
  - _Design: Overview (STT decision), Risk 1_

### 4. تمرير معاملات الجودة عبر نقطة المعاينة

- [x] 17. مرّر `thin_line_min_width_mm` و`enable_face_handling` عبر `preview_sketch_centerline`
  - أضف `thin_line_min_width_mm: Optional[float] = Form(None)` (افتراضي 0.0، مدى 0.0–6.0 عبر `_coerce_float`) و`enable_face_handling: Optional[bool] = Form(None)` (افتراضي True) لتوقيع `preview_sketch_centerline`.
  - مرّرهما لـ `vectorize_sketch_image_to_plan` وأضفهما لـ `sketch_parameters`.
  - حدّث استدعاء `generate_preview` (multipart) ليقرأ المفتاحين من `settings` ويمرّرهما (نمط بقية المفاتيح).
  - تأكّد أن غياب المفتاحين = السلوك الحالي تماماً (توافق خلفي / Property 6).
  - _Requirements: 8.1, 8.5, 7.2_
  - _Design: Data Models (pipeline params), Component 7, Component 8_

- [x] 18. اكتب اختبار endpoint للمعاملات الجديدة
  - أضف لملف endpoint المعتمد على httpx: طلب معاينة مع `thin_line_min_width_mm` كبير → عدد سكتات أقل (أو تحذير "nothing to draw" عند حذف الكل)؛ وطلب بدون المفتاحين = سلوك افتراضي.
  - تأكّد من ظهور تحذير عند إفراغ كل السكتات (AC 8.7) في حقل warnings للاستجابة.
  - _Requirements: 8.5, 8.7_
  - _Design: Testing Strategy_

### 5. تنظيف إعدادات السكتش (Frontend)

- [ ] 19. أخفِ الإعدادات المتقدمة عديمة القيمة من الواجهة
  - في `src/wall_climber/web/index.html`: احذف/أخفِ من DOM عناصر `Advanced sketch settings` المحدّدة في جدول التصميم (Curve Tolerance, Margin, Noise Area, Line Sensitivity, Min Stroke Length, Skeleton Prune, Path Optimizer, Stroke Merge Gap, Simplify Epsilon, Tiny Detect Max, Sketch Scale, Sketch Center X/Y).
  - عدّل `readFilePreviewSettings()` بحيث لا يرسل هذه المفاتيح (يتركها للـ backend defaults)، مع إبقاء `Processing Resolution`, `Path Smoothness`, `Detail Level`, `Sketch Extraction Method`, `Fit Safe`, `Optimize Stroke Order`.
  - أبقِ السلوك متوافقاً: تأكّد أن المعاينة لنفس الصورة بعد الإخفاء مطابقة لمعاينة بالقيم الافتراضية (Property 6).
  - _Requirements: 6.4, 6.5, 6.6, 6.7, 6.8_
  - _Design: تنظيف إعدادات السكتش, Property 6_

- [ ] 20. أضف نصوص شرح للإعدادات المُبقاة
  - أضف شرحاً قصيراً واضحاً لـ `Processing Resolution` (تأثير الدقة على التفاصيل/السرعة) و`Path Smoothness` (Normal=منحنيات ناعمة / Off=خطوط مستقيمة) كـ field-hint بجانب كل عنصر.
  - _Requirements: 6.1, 6.2, 6.3_
  - _Design: تنظيف إعدادات السكتش_

- [ ] 21. أضف سلايدر فلتر الخطوط الرفيعة للواجهة
  - أضف `#sketch-thin-line` (range: min=0, max=6, step=0.5, value=0) + `#sketch-thin-line-readout` في لوحة الملف، مع شرح "يحذف الخطوط الأرفع من قلم الروبوت".
  - اربطه في `dom`، وأضف `thin_line_min_width_mm` لـ `readFilePreviewSettings()`، واربط `input/change` بـ `markPreviewSettingsChanged()` + تحديث الـ readout (نمط `syncSketchResolutionReadout`).
  - عند رجوع تحذير "nothing to draw" من الـ backend، اعرضه في `#sketch-preview-warning` (AC 8.7).
  - _Requirements: 8.1, 8.2, 8.5, 8.7_
  - _Design: Component 7 (UI), Data Models (UI elements)_

### 6. زر الإجراء الواحد (Frontend)

- [ ] 22. ادمج تدفّق النص في إجراء واحد
  - في `index.html`: احذف زر `#text-preview-btn` ("Generate Preview")؛ أعد تسمية/استخدم زراً واحداً (`#text-submit-btn` مثلاً "Write") يستدعي دالة جديدة `commitText()` التي تنفّذ preview ثم draw تسلسلياً بنفس `preview_id`.
  - `commitText()`: استدعِ `/api/preview` (input_type text) → خزّن `preview_id` → `ensureMode('text')` → `/api/draw`. عند فشل أي خطوة: رسالة خطأ وصفية والوضع لا يتغيّر (AC 5.5).
  - أزل اعتماد الزر على `previewDirty` كشرط مانع (الإجراء الواحد يولّد معاينة طازجة دائماً).
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_
  - _Design: Component 4 (SingleActionText), Property 4_

- [ ] 23. ادمج تدفّق الملف في إجراء واحد مماثل (متّسق)
  - طبّق نفس نمط الإجراء الواحد على لوحة الملف عبر `commitSketch()` (preview ثم draw)، مع إبقاء معاينة منفصلة فقط إذا لزمت لضبط السلايدرات. إن تعارض مع تجربة ضبط الإعدادات، أبقِ المعاينة منفصلة للملف واكتفِ بالإجراء الواحد للنص.
  - وثّق القرار في تعليق بالكود.
  - _Requirements: 5.2_
  - _Design: Component 4_

### 7. التحكّم الصوتي (Frontend)

- [ ] 24. أضف عناصر واجهة الصوت
  - أضف زر `#voice-capture-btn` (ميكروفون) ومؤشّر `#voice-recording-indicator` وحاوية النص `#voice-transcript` (clickable) وعدّاد `#voice-countdown` في موضع مناسب من لوحة الإدخال.
  - أظهِر زر الميكروفون فقط عندما `Active_Mode ∈ {text, draw}` (اربطه بمنطق تحديث الوضع الموجود).
  - اربط كل العناصر الجديدة في كائن `dom`.
  - _Requirements: 1.1_
  - _Design: Component 1 (VoiceController UI)_

- [ ] 25. نفّذ `VoiceController` (التقاط + تفريغ)
  - أضف كائن `VoiceController` (نمط JS الموجود): `toggle/startRecording/stopRecording` عبر `getUserMedia({audio})` + `MediaRecorder`؛ تجميع chunks → Blob.
  - `transcribe(blob)`: `POST /api/voice/transcribe` (multipart). عند 503 أو فشل الشبكة → `transcribeViaWebSpeech()` (إن كان `webkitSpeechRecognition`/`SpeechRecognition` متاحاً)، وإلا رسالة واضحة.
  - معالجة الأخطاء: رفض إذن/لا جهاز → رسالة وصفية (AC 1.8)؛ نص فارغ → "no speech recognized" دون تغيير الوضع (AC 1.7)؛ مؤشّر تسجيل مرئي أثناء الالتقاط (AC 1.2, 1.3).
  - عند الحصول على نص: نادِ `VoiceCommandRouter.route(text)`.
  - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.7, 1.8_
  - _Design: Component 1, Property 8_

- [ ] 26. نفّذ `VoiceCommandRouter`
  - أضف `VoiceCommandRouter.route(transcript)`: طبّع نسخة `trim().toLowerCase()` **للقرار فقط**.
  - "text mode"/"draw mode" → `requestMode()` عبر `setMode()` الموجودة (يورّث 409/503 ورسائلها) (AC 2.1–2.5).
  - في draw mode: `^draw\s+picture\s+number\s+(\d+)$` → `drawLibrary(N)` عبر `POST /api/draw_library/N`، وعرض رسالة "picture number N not found" عند 404 (AC 4.2, 4.5).
  - في text mode وغير أمر → مرّر النص **الحرفي (غير منخفض الأحرف)** لـ `TextDictationController.present()` (AC 3.1, Property 1).
  - draw mode + غير مطابق → لا إجراء (AC 2.6).
  - _Requirements: 2.1, 2.2, 2.3, 2.6, 4.2, 1.5_
  - _Design: Component 2 (VoiceCommandRouter), Property 1, Property 3_

- [ ] 27. نفّذ `TextDictationController` (نافذة الإلغاء 5 ثوانٍ)
  - أضف `TextDictationController` مع `present/startCountdown/cancelPending/commit`.
  - `present(transcript)`: ألغِ أي معلّق سابق، اعرض النص الحرفي في `#voice-transcript`، ابدأ عدّاً تنازلياً مرئياً 5→0 في `#voice-countdown`.
  - نقر `#voice-transcript` → `cancelPending()` (يلغي الكتابة، يبقي النص ظاهراً وقابلاً للتحرير في `#text-input`) (AC 3.4, 3.5).
  - transcript جديد أثناء العدّ → `present()` يلغي ويعيد البدء (AC 3.6).
  - بلوغ 0 → `commit()` يستدعي `commitText()` (الإجراء الواحد) فيكتب النص الحرفي بالضبط (AC 3.2, 3.3, 3.7).
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
  - _Design: Component 3 (TextDictationController), Property 1, Property 2_

- [ ] 28. اربط أحداث الواجهة الصوتية ودورة حياة الوضع
  - اربط `#voice-capture-btn` بـ `VoiceController.toggle`؛ تأكّد من إخفاء/إظهار العناصر الصوتية عند تبديل الوضع؛ أوقف أي عدّاد معلّق عند مغادرة text mode.
  - تأكّد أن تحديث `Active_Mode` بعد أمر صوتي ينعكس على أزرار الوضع خلال ثانية (AC 2.4) عبر نفس مسار تحديث الحالة الموجود.
  - _Requirements: 2.4, 1.1_
  - _Design: Component 1, Component 2_

### 8. التحقّق النهائي والتغليف

- [ ] 29. شغّل مجموعة الاختبارات الكاملة وأصلح أي إخفاقات
  - شغّل أمر pytest المعتمد؛ تأكّد من نجاح كل الاختبارات الجديدة والقديمة (الوحدات تعمل بدون httpx؛ اختبارات الـ endpoints الجديدة في ملفات httpx).
  - _Requirements: 1.4, 4.4, 7.6, 8.6_
  - _Design: Testing Strategy_

- [ ] 30. أعد بناء الحزمة وتحقّق من التثبيت
  - `colcon build --packages-select wall_climber`؛ تحقّق من تثبيت: الوحدات الجديدة في `lib/.../wall_climber/`، أصول `assets/draw_library/` (manifest.json + examples) في `share/`، و`index.html` المحدّث في `share/.../web/`.
  - شغّل الـ launch يدوياً وتأكّد بصرياً: زر الميكروفون يظهر في text/draw، تنظيف الإعدادات، سلايدر الخطوط، وزر النص الواحد.
  - _Requirements: 1.1, 4.1, 6.4, 8.1_
  - _Design: Overview (layers), Testing Strategy_

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": [1, 4, 7, 12, 16],
      "description": "وحدات معزولة بلا تبعيات: فلتر الخطوط، وحدة الوجه، خدمة المكتبة، وحدة التفريغ، وإضافة الاعتمادية."
    },
    {
      "wave": 2,
      "tasks": [2, 5, 8, 9, 13],
      "description": "اختبارات الوحدات الجديدة + إنشاء manifest.json والأمثلة (تعتمد على وحدات الموجة 1)."
    },
    {
      "wave": 3,
      "tasks": [3, 6, 10, 14],
      "description": "دمج وحدات الـ pipeline والـ endpoints الخلفية (تعتمد على الوحدات واختباراتها)."
    },
    {
      "wave": 4,
      "tasks": [11, 15, 17],
      "description": "اختبارات endpoints المكتبة/التفريغ + تمرير معاملات الجودة عبر preview (يعتمد على 3 و6 و10 و14)."
    },
    {
      "wave": 5,
      "tasks": [18, 19, 20, 22, 24],
      "description": "اختبار معاملات preview + بداية الواجهة: إخفاء الإعدادات، نصوص الشرح، إجراء النص الواحد، عناصر الصوت."
    },
    {
      "wave": 6,
      "tasks": [21, 23, 25],
      "description": "سلايدر الخطوط (يحتاج 17)، إجراء الملف الواحد (يحتاج 22)، VoiceController (يحتاج 14)."
    },
    {
      "wave": 7,
      "tasks": [26, 27],
      "description": "VoiceCommandRouter (يحتاج 10 و25) و TextDictationController (يحتاج 22 و25)."
    },
    {
      "wave": 8,
      "tasks": [28],
      "description": "ربط أحداث الواجهة الصوتية ودورة حياة الوضع (يحتاج 24–27)."
    },
    {
      "wave": 9,
      "tasks": [29, 30],
      "description": "تشغيل كل الاختبارات، ثم البناء والتثبيت والفحص البصري النهائي."
    }
  ]
}
```

ترتيب التنفيذ التسلسلي الموصى به إن نُفّذت مهمة-مهمة: المسار 1 (1→2→3، 4→5→6)، ثم المسار 2 (7→8→9→10→11)، ثم المسار 3 (12→13→14→15، 16)، ثم 17→18، ثم الواجهة (19→20، 21، 22→23، 24→25→26→27→28)، وأخيراً 29→30.

## Notes

- **مبدأ إعادة الاستخدام**: كل مهام الرسم/الكتابة تنتهي عند `publish_execution_plan` الموجود؛ لا تُنشأ مسارات نشر بديلة (Property 4).
- **التوافق الخلفي**: المعاملات الجديدة (`thin_line_min_width_mm`, `enable_face_handling`) افتراضياتها تساوي السلوك الحالي تماماً، فالطلبات القديمة لا تتأثّر (Property 6).
- **التدهور اللطيف**: غياب `faster-whisper` أو Haar cascade أو ffmpeg لا يكسر النظام (503 + fallback للـ Web Speech API / "لا وجوه") — يجب التحقّق من هذا في المهام 12، 14، 25، 4 (Property 8).
- **اختبارات الواجهة يدوية**: لا يوجد إطار اختبار JS؛ التحقّق من VoiceController/DictationController/الإجراء الواحد عبر فحص يدوي في المهمة 30 بسيناريوهات القبول.
- **اختبارات httpx**: اختبارات الـ endpoints الجديدة (11، 15، 18) تُضاف للملفات المعتمدة على `TestClient`/httpx (تتبع نمط الملفات المستثناة من أمر التشغيل المحلي) لتعمل في بيئات CI التي فيها httpx.
- **حدود النطاق**: تحديد منطقة الوجه يدوياً (عند فشل Haar على الرسوم الكرتونية) خارج نطاق هذه الدفعة (Design Risk 3). معيار الدقة 15% WER (AC 1.6) يُقاس بفحص يدوي لا اختبار آلي.
- بعد كل مهمة backend: شغّل أمر pytest المعتمد و`colcon build`. لا توجد تغييرات URDF فلا حاجة للتحقّق بـ `xacro`.
