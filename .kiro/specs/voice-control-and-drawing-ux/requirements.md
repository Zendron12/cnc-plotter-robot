# Requirements Document

## Introduction

هذه الميزة (`voice-control-and-drawing-ux`) تضيف طبقة تحكم صوتي وتحسينات في تجربة الاستخدام (UX) إلى مشروع الروبوت الراسم `wall_climber` المبني على ROS2 + Webots. الهدف هو تمكين المستخدم من التحكم بالروبوت عبر الصوت باللغة الإنجليزية (تبديل الأوضاع، كتابة النص، ورسم صور مُسمّاة من مكتبة)، إضافةً إلى تبسيط واجهة الويب وتنظيف إعدادات الرسم المتقدمة وتحسين جودة رسم الوجوه البشرية ومعالجة مشكلة الخطوط الرفيعة الناتجة عن عرض القلم الثابت.

تبني الميزة على البنية القائمة:
- واجهة الويب: `src/wall_climber/web/index.html` (صفحة واحدة فيها وضع النص، وضع الرسم، إعدادات السكتش، وأزرار `Generate Preview` و`Draw`).
- الخادم الخلفي: `src/wall_climber/wall_climber/web_server.py` (FastAPI؛ يحتوي `/api/face/text`، `/api/face/expression`، تبديل الوضع عبر `ACTIVE_MODE_TOPIC`، نقاط `/api/preview` و`/api/draw`، ونقطة معاينة/رسم السكتش `preview_sketch_centerline`).
- مواضيع ROS: `src/wall_climber/wall_climber/runtime_topics.py` (`MODE_DRAW`, `MODE_TEXT`, `MODE_OFF`, `ACTIVE_MODE_TOPIC`).
- خط معالجة الصورة/السكتش: `src/wall_climber/wall_climber/image_pipeline/sketch_centerline.py`.
- الإعداد الفيزيائي: `src/wall_climber/config/cable_robot.yaml` (نصف قطر سن القلم `pen.tip_radius = 0.003 m`، أي عرض سكتة ثابت ≈ 6 mm).
- مكتبة الصور المُسمّاة: `assets/draw_library/` مع `manifest.example.json`.

ملاحظة تصميمية: المتطلبات أدناه تصف **ماذا** يجب أن يفعله النظام وبأي معايير قبول قابلة للاختبار، وليس **كيف** سيُنفَّذ. القرارات التقنية (مثل اختيار محرك التعرف على الكلام، أو خوارزمية كشف الوجه) تُترك لوثيقة التصميم.

## Glossary

- **Web_UI**: واجهة المستخدم في المتصفح المُقدَّمة من `index.html`.
- **Web_Backend**: خادم FastAPI في `web_server.py` المسؤول عن نقاط REST ونشر مواضيع ROS.
- **Voice_Capture_Component**: مكوّن واجهة الويب المسؤول عن زر الميكروفون والتقاط الصوت المنطوق.
- **Speech_Transcriber**: المكوّن (في المتصفح أو خادم ذاتي الاستضافة) الذي يحوّل الصوت الإنجليزي المنطوق إلى نص (transcript).
- **Voice_Command_Router**: المنطق الذي يصنّف النص المُتعرَّف عليه إلى أمر (تبديل وضع، رسم صورة مُسمّاة) أو نص حرفي للكتابة.
- **Active_Mode**: الوضع التشغيلي الحالي للروبوت، إحدى القيم `MODE_OFF` / `MODE_TEXT` / `MODE_DRAW` المنشورة على `ACTIVE_MODE_TOPIC`.
- **Text_Write_Controller**: المنطق الذي يدير تدفّق كتابة النص المنطوق بما في ذلك نافذة الإلغاء ذات الخمس ثوانٍ.
- **Cancel_Window**: الفترة الزمنية البالغة 5 ثوانٍ بعد عرض النص المُتعرَّف عليه والتي يمكن خلالها للمستخدم إلغاء الكتابة.
- **Draw_Library**: مجلد الصور المُسبقة الإعداد `assets/draw_library/` الذي يضع فيه المستخدم صوره، مع ملف فهرسة `manifest`.
- **Draw_Library_Service**: المنطق الذي يربط الاسم/الرقم المنطوق بملف صورة داخل `Draw_Library`.
- **Sketch_Pipeline**: خط المعالجة في `sketch_centerline.py` الذي يحوّل صورة إلى مسارات خطية (centerline).
- **Sketch_Settings**: مجموعة إعدادات معالجة السكتش في `Web_UI` و`preview_sketch_centerline`.
- **Advanced_Sketch_Settings**: القسم القابل للطيّ `#advanced-sketch-settings` في `Web_UI` الذي يحوي الإعدادات منخفضة المستوى.
- **Face_Region**: منطقة الوجه البشري داخل صورة المصدر (العينان، الأنف، الفم).
- **Pen_Stroke_Width**: عرض السكتة الثابت للقلم الفيزيائي، مُشتقّ من `pen.tip_radius = 0.003 m` في `cable_robot.yaml`.
- **Thin_Line_Filter**: المرشّح القابل للتحكم من المستخدم الذي يستبعد الخطوط الأرفع/الأبهت من عتبة محددة.
- **Preview_Id**: المعرّف الذي تُعيده نقطة المعاينة ويُستخدم لاحقًا في `/api/draw`.

---

## Requirements

### Requirement 1: زر الإدخال الصوتي والتعرّف على الكلام الإنجليزي

**User Story:** كمستخدم للروبوت الراسم، أريد زرّ ميكروفون في `Web_UI` يلتقط كلامي الإنجليزي ويحوّله إلى نص دقيق ويعرضه لي حرفيًا، حتى أتمكن من التحكم بالروبوت وكتابة النص دون استخدام لوحة المفاتيح.

#### Acceptance Criteria

1. THE Web_UI SHALL display a microphone button (`voice-capture-btn`) that is visible while `Active_Mode` is `MODE_TEXT` or `MODE_DRAW`.
2. WHEN the user activates the microphone button, THE Voice_Capture_Component SHALL begin capturing audio and display a recording-active indicator.
3. WHILE audio capture is active, THE Web_UI SHALL display a visible recording state until capture stops or the user stops it.
4. WHEN audio capture completes, THE Speech_Transcriber SHALL produce an English text transcript of the spoken audio.
5. WHEN a transcript is produced, THE Web_UI SHALL display the transcript text verbatim, without paraphrasing, translating, or reformatting the recognized words.
6. THE Speech_Transcriber SHALL achieve a word error rate of 15 percent or lower on clear English speech recorded in a quiet environment, measured against a fixed reference test set of spoken phrases.
7. IF the Speech_Transcriber produces no recognizable words, THEN THE Web_UI SHALL display a "no speech recognized" message and SHALL leave `Active_Mode` unchanged.
8. IF microphone permission is denied or no audio input device is available, THEN THE Web_UI SHALL display a descriptive error message identifying the cause.
9. WHERE a self-hosted transcription model is used instead of a browser speech API, THE Web_Backend SHALL expose a transcription endpoint that accepts captured audio and returns the English transcript text.

### Requirement 2: الأوامر الصوتية لتبديل الوضع

**User Story:** كمستخدم، أريد أن أقول "text mode" أو "draw mode" فيتحوّل الروبوت إلى الوضع المقابل، حتى أبدّل الأوضاع دون لمس الواجهة.

#### Acceptance Criteria

1. WHEN the recognized transcript matches the command phrase "text mode", THE Voice_Command_Router SHALL request a mode switch to `MODE_TEXT`.
2. WHEN the recognized transcript matches the command phrase "draw mode", THE Voice_Command_Router SHALL request a mode switch to `MODE_DRAW`.
3. WHEN a mode switch is requested by the Voice_Command_Router, THE Web_Backend SHALL publish the target mode on `ACTIVE_MODE_TOPIC` using the existing `switch_mode` mechanism in `web_server.py`.
4. WHEN a voice-initiated mode switch succeeds, THE Web_UI SHALL reflect the new `Active_Mode` in the runtime mode controls within 1 second of the switch being confirmed.
5. IF a voice-initiated mode switch is requested WHILE `cable_executor_status` is `running`, THEN THE Web_Backend SHALL reject the switch with HTTP status 409 and THE Web_UI SHALL display a "runtime is busy" message.
6. IF the recognized transcript does not match a defined command phrase AND `Active_Mode` is not `MODE_TEXT`, THEN THE Voice_Command_Router SHALL take no mode-switch action.

### Requirement 3: تدفّق كتابة النص المُملى صوتيًا مع نافذة الإلغاء

**User Story:** كمستخدم في وضع النص، أريد أن يظهر النص الذي قلته حرفيًا، ثم يبدأ عدّ تنازلي مدته 5 ثوانٍ يكتب بعده الروبوت ما عُرض بالضبط، مع إمكانية إلغاء الكتابة بالنقر على النص خلال هذه الثواني إذا أخطأت في النطق.

#### Acceptance Criteria

1. WHILE `Active_Mode` is `MODE_TEXT`, WHEN the Speech_Transcriber produces a transcript that is not a mode-switch command, THE Web_UI SHALL display the transcript text verbatim as the pending write text.
2. WHEN the pending write text is displayed, THE Text_Write_Controller SHALL start a 5-second `Cancel_Window` and THE Web_UI SHALL display a visible countdown from 5 seconds to 0.
3. WHEN the 5-second `Cancel_Window` elapses without a cancel action, THE Text_Write_Controller SHALL submit the displayed text for writing so that the robot writes exactly the displayed characters.
4. WHEN the user clicks or taps the displayed pending write text WHILE the `Cancel_Window` is active, THE Text_Write_Controller SHALL cancel the pending write and the robot SHALL NOT write the text.
5. WHEN a pending write is cancelled, THE Web_UI SHALL retain the cancelled transcript text visible so the user can re-record or edit it.
6. WHILE a `Cancel_Window` is active, IF a new transcript is produced, THEN THE Text_Write_Controller SHALL cancel the current pending write and start a new `Cancel_Window` for the new transcript.
7. WHEN the Text_Write_Controller submits text for writing, THE Web_Backend SHALL write only the displayed text and SHALL NOT modify its characters.

### Requirement 4: تدفّق الرسم المُملى صوتيًا من المكتبة المُسمّاة

**User Story:** كمستخدم في وضع الرسم، أريد أن أقول "draw picture number 1" فيرسم الروبوت الصورة المخزّنة باسم "1" في مجلد صوري المُسبق الإعداد، حتى أرسم صورًا جاهزة بالأمر الصوتي.

#### Acceptance Criteria

1. THE Draw_Library SHALL be a user-managed folder at `assets/draw_library/` whose images are indexed by a numeric identifier via a `manifest` file following the shape of `manifest.example.json`.
2. WHILE `Active_Mode` is `MODE_DRAW`, WHEN the recognized transcript matches the grammar "draw picture number N" where N is a positive integer, THE Voice_Command_Router SHALL resolve N to the matching `Draw_Library` entry identifier.
3. WHEN a `Draw_Library` entry for the requested identifier N exists, THE Draw_Library_Service SHALL load the entry image and submit it through the existing image-to-plan pipeline used for uploaded images, producing a `Preview_Id`.
4. WHEN the resolved image plan is ready, THE Web_Backend SHALL draw it using the same execution path as a uploaded-image draw via `/api/draw`.
5. IF the requested identifier N has no matching entry in the `Draw_Library` manifest, THEN THE Draw_Library_Service SHALL take no drawing action and THE Web_UI SHALL display a message stating that picture number N was not found.
6. IF the manifest entry for identifier N references a file that is missing or unreadable, THEN THE Draw_Library_Service SHALL take no drawing action and THE Web_UI SHALL display a descriptive error identifying the missing file.
7. IF a voice-initiated draw is requested WHILE `cable_executor_status` is `running`, THEN THE Web_Backend SHALL reject the draw with HTTP status 409 and THE Web_UI SHALL display a "runtime is busy" message.

### Requirement 5: تبسيط واجهة وضع النص إلى إجراء واحد

**User Story:** كمستخدم في وضع النص، أريد زرّ إجراء واحدًا يكتب النص مباشرةً، حتى لا أضطر للضغط على `Generate Preview` ثم `Draw` في خطوتين.

#### Acceptance Criteria

1. THE Web_UI SHALL remove the separate `Generate Preview` button (`text-preview-btn`) from the text input panel.
2. THE Web_UI SHALL provide a single text action control that performs preview generation and drawing as one user action.
3. WHEN the user activates the single text action control, THE Web_Backend SHALL generate the text `Preview_Id` and submit the draw for that `Preview_Id` within the same request flow, without requiring a second user action.
4. WHEN the text content has not changed since the last successful draw, THE Web_UI SHALL allow the single text action control to draw without requiring the user to regenerate a preview manually.
5. IF text drawing fails after the single action is triggered, THEN THE Web_UI SHALL display a descriptive error message and SHALL leave `Active_Mode` unchanged.

### Requirement 6: توضيح وتنظيف إعدادات الرسم

**User Story:** كمستخدم يرفع صورًا للرسم، أريد إعدادات سكتش واضحة ومفيدة فقط، حتى لا تربكني خيارات منخفضة المستوى بلا قيمة واضحة.

#### Acceptance Criteria

1. THE Web_UI SHALL retain the `Processing Resolution` control with its four steps (700 px, 1000 px default, 1300 px, 1500 px) and SHALL display a readout label and descriptive hint for the selected step.
2. THE Web_UI SHALL retain the `Path Smoothness` control with its options `Normal` (`smooth_curves`) and `Off` (`polyline`).
3. THE Web_UI SHALL display a short descriptive label for each retained `Sketch_Settings` control explaining its effect in user-facing terms.
4. THE Web_UI SHALL hide from the user the following `Advanced_Sketch_Settings` controls, which provide no user-facing value, and SHALL apply their backend default values automatically: `Curve Tolerance (px)`, `Noise Area (px)`, `Line Sensitivity`, `Min Stroke Length (px)`, `Skeleton Prune (px)`, `Path Optimizer`, `Stroke Merge Gap (px)`, `Simplify Epsilon (px)`, `Tiny Detect Max (mm)`, `Sketch Scale (%)`, `Sketch Center X (m)`, and `Sketch Center Y (m)`.
5. WHEN any `Advanced_Sketch_Settings` control is hidden, THE Web_Backend SHALL use the documented default for that parameter in `preview_sketch_centerline` (for example `curve_tolerance_px = 0.6`, `skeleton_prune_px = 6.0`, `path_optimizer = internal`, `min_component_area_px = 2`, `line_sensitivity = 0.35`, `simplify_epsilon_px = 0.25`, `scale_percent = 100`).
6. WHEN an advanced parameter value equals its documented default value, THE Web_Backend SHALL apply the backend default for that parameter regardless of whether the corresponding control is visible.
7. WHEN the hidden advanced settings are removed from the request, THE Web_Backend SHALL produce a preview equivalent to the prior default-valued request for the same image and retained settings.
8. THE Web_UI SHALL retain the `Margin (m)` value as an automatically applied backend default and SHALL NOT require the user to set it manually.

### Requirement 7: جودة رسم الوجوه البشرية

**User Story:** كمستخدم أرسم صور بورتريه/رسوم توضيحية، أريد أن تُرسم الوجوه البشرية بدقة مقبولة، حتى لا تتحوّل العينان والفم إلى كتل مشوّهة بينما يخرج باقي الجسم/المشهد جيدًا.

#### Acceptance Criteria

1. WHEN a source image containing a human face is traced, THE Sketch_Pipeline SHALL produce facial feature strokes (eyes, nose, mouth) whose shapes remain recognizable as those features rather than collapsing into filled blobs.
2. WHEN a `Face_Region` is detected within a source image, THE Sketch_Pipeline SHALL apply feature-preserving processing to that region.
3. WHILE processing a detected `Face_Region`, THE Sketch_Pipeline SHALL preserve open outlines for eyes and mouth so that enclosed features are not filled solid.
4. WHEN the same portrait image is traced, the produced facial output SHALL achieve a measurable similarity to the source face that meets or exceeds an agreed fidelity threshold on a fixed portrait test set.
5. IF no `Face_Region` is detected in the source image, THEN THE Sketch_Pipeline SHALL process the image using its standard centerline behavior without face-specific handling.
6. WHEN face-specific processing is applied, THE Sketch_Pipeline SHALL preserve the existing tracing quality of non-face regions (body and scene) at the level produced without face handling.

### Requirement 8: مرشّح الخطوط الرفيعة القابل للتحكم

**User Story:** كمستخدم، أريد منزلقًا (slider) في `Web_UI` يستبعد الخطوط الأرفع/الأبهت من عتبة أحددها، حتى يحتوي الرسم النهائي فقط على سكتات يستطيع القلم ذو العرض الثابت رسمها بشكل جيد.

#### Acceptance Criteria

1. THE Web_UI SHALL provide a `Thin_Line_Filter` slider control in the file/image input panel.
2. THE Thin_Line_Filter slider SHALL expose a minimum value, a maximum value, and a documented default value, where the minimum disables filtering (keeps all lines) and the maximum applies the strongest thin-line removal.
3. WHEN the `Thin_Line_Filter` value is greater than its minimum, THE Sketch_Pipeline SHALL exclude source lines whose stroke thickness or faintness falls below the threshold derived from the slider value.
4. THE Thin_Line_Filter threshold SHALL be interpreted relative to `Pen_Stroke_Width` (derived from `pen.tip_radius = 0.003 m`) so that retained strokes are renderable by the physical pen.
5. WHEN the user changes the `Thin_Line_Filter` value, THE Web_UI SHALL update the executable preview to reflect the new filtering within the existing preview-refresh flow, including when filtering removes all strokes.
6. WHEN the `Thin_Line_Filter` value is at its minimum, THE Sketch_Pipeline SHALL retain all non-zero-thickness lines that the pipeline would otherwise produce without the filter, and SHALL exclude zero-thickness strokes as invalid.
7. IF applying the `Thin_Line_Filter` removes all strokes from the image, THEN THE Web_UI SHALL display a warning that the current filter value leaves nothing to draw.
