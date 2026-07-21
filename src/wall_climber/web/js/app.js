    (() => {
      'use strict';

      // Rosbridge is proxied through the FastAPI backend, so VS Code only
      // needs to forward the web UI port.
      const WS_SCHEME = location.protocol === 'https:' ? 'wss' : 'ws';
      const WS_HOST = location.host || '127.0.0.1:8080';
      const WS_URL = `${WS_SCHEME}://${WS_HOST}/rosbridge`;
      const DEBUG_MODE = new URLSearchParams(window.location.search).get('debug') === '1'
        || window.localStorage.getItem('debugMode') === 'true';
      document.body.classList.toggle('debug-mode', DEBUG_MODE);
      const RUNTIME_POLL_MS = 1500;
      const DEFAULT_BOARD = {
        width: 6.3,
        height: 3.0,
        writable_x_min: 0.1,
        writable_x_max: 6.2,
        writable_y_min: 0.1,
        writable_y_max: 2.9,
        safe_x_min: 0.16,
        safe_x_max: 6.14,
        safe_y_min: 0.22,
        safe_y_max: 2.82,
        body_safe_x_min: 0.348,
        body_safe_x_max: 6.2,
        body_safe_y_min: 0.12,
        body_safe_y_max: 2.9,
        line_height: 0.150,
        glyph_height: 0.086,
        corner_keepout_radius: 0.24,
        anchors: {
          left: { x: 0, y: 0 },
          right: { x: 6.3, y: 0 },
        },
      };
      const CARRIAGE = {
        width: 0.29,
        height: 0.20,
        attachX: 0.104,
        attachY: 0.075,
        armBaseX: 0.129,
        armMidX: 0.161,
        wristX: 0.189,
        holderX: 0.203,
        armOffsetY: 0.020,
        penOffsetX: 0.203,
        penOffsetY: 0.020,
      };
      const MAX_TRAIL_POINTS = 120000;
      const MIN_TRAIL_POINT_DIST = 0.0015;

      const TOPICS = {
        boardInfo: '/wall_climber/board_info',
        robotPose: '/wall_climber/robot_pose_board',
        penPose: '/wall_climber/pen_pose_board',
        penContact: '/wall_climber/pen_contact',
      };

      const dom = {
        backendPill: document.getElementById('backend-pill'),
        backendText: document.getElementById('backend-text'),
        rosbridgePill: document.getElementById('rosbridge-pill'),
        rosbridgeText: document.getElementById('rosbridge-text'),
        runtimePill: document.getElementById('runtime-pill'),
        runtimeText: document.getElementById('runtime-text'),
        summaryBoard: document.getElementById('summary-board'),
        summaryPreview: document.getElementById('summary-preview'),
        stripBoard: document.getElementById('strip-board'),
        stripPreview: document.getElementById('strip-preview'),
        stripTrail: document.getElementById('strip-trail'),
        notice: document.getElementById('notice'),
        noticeTitle: document.getElementById('notice-title'),
        noticeCopy: document.getElementById('notice-copy'),
        boardStage: document.getElementById('board-stage'),
        canvas: document.getElementById('board-canvas'),
        boardEditCanvas: document.getElementById('board-edit-canvas'),
        boardFabStack: document.getElementById('board-fab-stack'),
        boardFabMain: document.getElementById('board-fab-main'),
        boardFabClearPreview: document.getElementById('board-fab-clear-preview'),
        boardFabBlank: document.getElementById('board-fab-blank'),
        boardFabConfirm: document.getElementById('board-fab-confirm'),
        boardFabPen: document.getElementById('board-fab-pen'),
        boardFabEraser: document.getElementById('board-fab-eraser'),
        boardFabCrop: document.getElementById('board-fab-crop'),
        boardFabFullscreen: document.getElementById('board-fab-fullscreen'),
        boardFabBrushSize: document.getElementById('board-fab-brush-size'),
        boardFabBrushLabel: document.getElementById('board-fab-brush-label'),
        boardFabBrushRange: document.getElementById('board-fab-brush-range'),
        boardFabBrushReadout: document.getElementById('board-fab-brush-readout'),
        boardFabBrushAnchorPen: document.getElementById('board-fab-brush-anchor-pen'),
        boardFabBrushAnchorEraser: document.getElementById('board-fab-brush-anchor-eraser'),
        boardFabVectorMethod: document.getElementById('board-fab-vector-method'),
        boardFabVectorButtons: Array.from(document.querySelectorAll('[data-board-vector-method]')),
        overlayCard: document.getElementById('overlay-card'),
        overlayTitle: document.getElementById('overlay-title'),
        overlayCopy: document.getElementById('overlay-copy'),
        previewChip: document.getElementById('preview-chip'),
        trailChip: document.getElementById('trail-chip'),
        clearTrailBtn: document.getElementById('clear-trail-btn'),
        metricBoard: document.getElementById('metric-board'),
        metricWritable: document.getElementById('metric-writable'),
        metricRobot: document.getElementById('metric-robot'),
        metricPen: document.getElementById('metric-pen'),
        statusExecutor: document.getElementById('status-executor'),
        statusSupervisor: document.getElementById('status-supervisor'),
        manualPenButtons: Array.from(document.querySelectorAll('[data-manual-pen]')),
        manualPenStatus: document.getElementById('manual-pen-status'),
        placementX: document.getElementById('placement-x'),
        placementY: document.getElementById('placement-y'),
        placementScale: document.getElementById('placement-scale'),
        placementResetBtn: document.getElementById('placement-reset-btn'),
        clearPreviewBtn: document.getElementById('clear-preview-btn'),
        placementCopy: document.getElementById('placement-copy'),
        placementXLabel: document.getElementById('placement-x-label'),
        placementYLabel: document.getElementById('placement-y-label'),
        placementHelper: document.getElementById('placement-helper'),
        toolButtons: Array.from(document.querySelectorAll('[data-tool]')),
        toolTextPanel: document.getElementById('tool-text-panel'),
        toolFilePanel: document.getElementById('tool-file-panel'),
        toolSvgPanel: document.getElementById('tool-svg-panel'),
        textInput: document.getElementById('text-input'),
        textFontSource: document.getElementById('text-font-source'),
        textLineHeight: document.getElementById('text-line-height'),
        textLineHeightReadout: document.getElementById('text-line-height-readout'),
        textColumnSeedGap: document.getElementById('text-column-seed-gap'),
        textColumnSeedGapReadout: document.getElementById('text-column-seed-gap-readout'),
        textColumnButtons: Array.from(document.querySelectorAll('[data-text-column]')),
        textSubmitBtn: document.getElementById('text-submit-btn'),
        textClearBtn: document.getElementById('text-clear-btn'),
        fileInput: document.getElementById('file-input'),
        fileDropZone: document.getElementById('file-drop-zone'),
        fileDropName: document.getElementById('file-drop-name'),
        fileUploadBtn: document.getElementById('file-upload-btn'),
        fileEditBoardBtn: document.getElementById('file-edit-board-btn'),
        fileDrawBtn: document.getElementById('file-draw-btn'),
        fileMeta: document.getElementById('file-meta'),
        sketchVectorizationMethod: document.getElementById('sketch-vectorization-method'),
        sketchPotraceHint: document.getElementById('sketch-potrace-hint'),
        sketchFitSafe: document.getElementById('sketch-fit-safe'),
        sketchOptimizeDraw: document.getElementById('sketch-optimize-draw'),
        sketchCurveTolerance: document.getElementById('sketch-curve-tolerance'),
        sketchMargin: document.getElementById('sketch-margin'),
        sketchScalePercent: document.getElementById('sketch-scale-percent'),
        sketchCenterX: document.getElementById('sketch-center-x'),
        sketchCenterY: document.getElementById('sketch-center-y'),
        sketchCurveFitTimeLimit: document.getElementById('sketch-curve-fit-time-limit'),
        autotraceSpeckleStrength: document.getElementById('autotrace-speckle-strength'),
        autotraceSpeckleReadout: document.getElementById('autotrace-speckle-readout'),
        autotraceSpeckleField: document.getElementById('autotrace-speckle-field'),
        imagePhotoLineartModel: document.getElementById('image-photo-lineart-model'),
        imagePhotoLineartModelField: document.getElementById('image-photo-lineart-model-field'),
        imageGoogleApiKey: document.getElementById('image-google-api-key'),
        imageGoogleApiKeyField: document.getElementById('image-google-api-key-field'),
        imageNanoBananaPrompt: document.getElementById('image-nano-banana-prompt'),
        imageNanoBananaPromptField: document.getElementById('image-nano-banana-prompt-field'),
        sketchDrawStatus: document.getElementById('sketch-draw-status'),
        pipelineModeLabel: document.getElementById('pipeline-mode-label'),
        advancedSketchSettings: document.getElementById('advanced-sketch-settings'),
        sketchExtractionMethod: document.getElementById('sketch-extraction-method'),
        sketchPreviewBox: document.getElementById('sketch-preview-box'),
        sketchPreviewTitle: document.getElementById('sketch-preview-title'),
        sketchPreviewNote: document.getElementById('sketch-preview-note'),
        sketchPreviewWarning: document.getElementById('sketch-preview-warning'),
        sketchPreviewImg: document.getElementById('sketch-preview-img'),
        previewMetricsGrid: document.getElementById('preview-metrics-grid'),
        sketchPreviewDiag: document.getElementById('sketch-preview-diag'),
        sketchOpenSvgBtn: document.getElementById('sketch-open-svg-btn'),
        sketchDownloadSvgBtn: document.getElementById('sketch-download-svg-btn'),
        sketchDownloadMetricsBtn: document.getElementById('sketch-download-metrics-btn'),
        imagePreprocessModeButtons: Array.from(document.querySelectorAll('[data-image-mode]')),
        imageRawPrintField: document.getElementById('image-raw-print-field'),
        imageRawPrint: document.getElementById('image-raw-print'),
        imageTargetResolution: document.getElementById('image-target-resolution'),
        imageTargetResolutionReadout: document.getElementById('image-target-resolution-readout'),
        imageTargetResolutionField: document.getElementById('image-target-resolution-field'),
        imageForceSolidBlack: document.getElementById('image-force-solid-black'),
        imageForceSolidBlackField: document.getElementById('image-force-solid-black-field'),
        pipelineStrip: document.getElementById('pipeline-strip'),
        pipelineStripRow: document.getElementById('pipeline-strip-row'),
        imageComparePanel: document.getElementById('image-compare-panel'),
        imageCompareTitle: document.getElementById('image-compare-title'),
        imageCompareViewport: document.getElementById('image-compare-viewport'),
        compareBefore: document.getElementById('compare-before'),
        compareAfter: document.getElementById('compare-after'),
        compareHandle: document.getElementById('compare-handle'),
        compareScrubber: document.getElementById('compare-scrubber'),
        svgInput: document.getElementById('svg-input'),
        svgPreviewBtn: document.getElementById('svg-preview-btn'),
        svgSubmitBtn: document.getElementById('svg-submit-btn'),
        svgClearBtn: document.getElementById('svg-clear-btn'),
        activityFeed: document.getElementById('activity-feed'),
        runtimeActiveMode: document.getElementById('runtime-active-mode'),
        runtimeReady: document.getElementById('runtime-ready'),
        runtimeBoardFrame: document.getElementById('runtime-board-frame'),
        runtimeSafeWorkspace: document.getElementById('runtime-safe-workspace'),
        runtimeNotReady: document.getElementById('runtime-not-ready'),
        runtimeWebotsTrail: document.getElementById('runtime-webots-trail'),
        previewSamplingDiag: document.getElementById('preview-sampling-diag'),
        runtimeSamplingDiag: document.getElementById('runtime-sampling-diag'),
        previewParityDiag: document.getElementById('preview-parity-diag'),
        canonicalPlanDiag: document.getElementById('canonical-plan-diag'),
        debugPlanPanel: document.getElementById('debug-plan-panel'),
        debugSamplingPanel: document.getElementById('debug-sampling-panel'),
        debugExecutionPanel: document.getElementById('debug-execution-panel'),
        debugCurveFitPanel: document.getElementById('debug-curve-fit-panel'),
        overlayRawToggle: document.getElementById('overlay-raw-toggle'),
        overlayCurvesToggle: document.getElementById('overlay-curves-toggle'),
        overlayFallbackToggle: document.getElementById('overlay-fallback-toggle'),
        overlayColorToggle: document.getElementById('overlay-color-toggle'),
        // Voice control
        emergencyStopBtn: document.getElementById('emergency-stop-btn'),
        voiceDeviceSelect: document.getElementById('voice-device-select'),
        voiceDeviceRefresh: document.getElementById('voice-device-refresh'),
        voiceCaptureBtn: document.getElementById('voice-capture-btn'),
        voiceCaptureLabel: document.getElementById('voice-capture-label'),
        voiceStatus: document.getElementById('voice-status'),
      };

      const ctx = dom.canvas.getContext('2d', { alpha: false });
      const editCtx = dom.boardEditCanvas ? dom.boardEditCanvas.getContext('2d', { alpha: true }) : null;
      const BoardFab = window.BoardFabUtils || {};
      const BOARD_RASTER_FIT_MARGIN_M = 0.05;
      const BRUSH_DIAMETER_MIN_MM = 1;
      const BRUSH_DIAMETER_MAX_MM = 24;
      const ERASER_DIAMETER_MAX_MM = 60;
      let suppressNextFileChangeClear = false;

      const state = {
        backend: 'connecting',
        rosbridge: 'connecting',
        runtime: null,
        manualPenMode: 'auto',
        board: null,
        robotPose: null,
        penPose: null,
        penContact: false,
        activeTool: 'text',
        placementTouched: false,
        vectorPreview: null,
        trailSegments: [],
        activeTrailSegment: null,
        trailPointCount: 0,
        lastTrailPoint: null,
        feedItems: [],
        debugPlan: null,
        debugExecution: null,
        debugCurveFit: null,
        reconnectTimer: null,
        runtimeTimer: null,
        previewRefreshTimer: null,
        previewInteraction: null,
        sketchPreviewId: null,
        sketchPreviewUrl: null,
        sketchPreviewSvgText: '',
        sketchPreviewPayload: null,
        lastSketchFile: null,
        sketchBoardOverlayImage: null,
        sketchBoardOverlayObjectUrl: null,
        sketchBoardOverlayLoaded: false,
        sketchBoardOverlayToken: 0,
        sketchPreviewBusy: false,
        sketchPreviewGeneration: 0,
        sketchDrawBusy: false,
        potraceAvailable: null,
        autotraceAvailable: null,
        aiPreprocessAvailable: null,
        cudaAvailable: null,
        anilinesWeightsCached: null,
        currentPreviewId: null,
        currentCanonicalHash: null,
        currentPrimitiveHash: null,
        currentExecutionHash: null,
        currentSettingsHash: null,
        currentExecutionPreviewSvg: '',
        currentMetrics: null,
        currentInput: null,
        currentProcessingSettings: null,
        currentPipelineMode: null,
        previewDirty: false,
        boardFabExpanded: false,
        boardFullscreen: false,
        boardRasterSession: null,
        boardEditMode: null,
        boardEditLastPoint: null,
        boardEditSmoothPoint: null,
        boardEditToolPointer: null,
        boardOverlayMode: null,
        boardFabConfirmBusy: false,
        penTipRadiusM: 0.003,
        eraserTipRadiusM: 0.005,
        textColumnDrafts: {
          full: { text: '', committed: '' },
          left: { text: '', committed: '' },
          center: { text: '', committed: '' },
          right: { text: '', committed: '' },
        },
        textWriteUndoStack: {
          full: [],
          left: [],
          center: [],
          right: [],
        },
        pendingWriteUndo: null,
      };

      const TEXT_COLUMNS = ['full', 'left', 'center', 'right'];

      function emptyColumnDraft() {
        return { text: '', committed: '' };
      }

      function ensureColumnDrafts() {
        TEXT_COLUMNS.forEach((column) => {
          if (!state.textColumnDrafts[column]) {
            state.textColumnDrafts[column] = emptyColumnDraft();
          }
        });
      }

      function persistCurrentColumnDraft() {
        ensureColumnDrafts();
        const column = readTextColumn();
        const draft = state.textColumnDrafts[column];
        draft.text = dom.textInput ? dom.textInput.value : '';
      }

      function loadColumnDraft(column) {
        ensureColumnDrafts();
        const normalized = TEXT_COLUMNS.includes(column) ? column : 'full';
        const draft = state.textColumnDrafts[normalized] || emptyColumnDraft();
        if (dom.textInput) {
          dom.textInput.value = draft.text;
        }
        state.textColumnDrafts[normalized] = draft;
      }

      function clearAllColumnDrafts() {
        state.textColumnDrafts = {
          full: emptyColumnDraft(),
          left: emptyColumnDraft(),
          center: emptyColumnDraft(),
          right: emptyColumnDraft(),
        };
        if (dom.textInput) {
          dom.textInput.value = '';
        }
      }

      function syncActiveColumnDraftFromTextarea() {
        ensureColumnDrafts();
        const column = readTextColumn();
        state.textColumnDrafts[column].text = dom.textInput ? dom.textInput.value : '';
      }

      async function clearCurrentColumnDraft() {
        ensureColumnDrafts();
        const column = readTextColumn();
        state.textColumnDrafts[column] = emptyColumnDraft();
        if (dom.textInput) {
          dom.textInput.value = '';
        }
        try {
          await resetBackendTextCursor(column);
        } catch (_error) {
          // Keep local clear even if backend is temporarily unavailable.
        }
        state.textWriteUndoStack[column] = [];
        if (state.pendingWriteUndo && state.pendingWriteUndo.column === column) {
          state.pendingWriteUndo = null;
        }
        if (state.currentInput === 'text') {
          markPreviewSettingsChanged();
        }
        pushFeed(`Cleared ${column} column draft.`, 'info');
      }

      function truncateTrailFromSegmentIndex(segmentIndex) {
        const idx = Math.max(0, Number(segmentIndex) || 0);
        if (idx >= state.trailSegments.length) {
          state.activeTrailSegment = null;
          state.lastTrailPoint = null;
          return;
        }
        state.trailSegments = state.trailSegments.slice(0, idx);
        state.trailPointCount = state.trailSegments.reduce((sum, segment) => sum + segment.length, 0);
        const lastSegment = state.trailSegments[state.trailSegments.length - 1];
        state.activeTrailSegment = null;
        state.lastTrailPoint = lastSegment && lastSegment.length
          ? lastSegment[lastSegment.length - 1]
          : null;
      }

      function canUndoLastWrite(column) {
        const key = TEXT_COLUMNS.includes(column) ? column : readTextColumn();
        if (state.pendingWriteUndo && state.pendingWriteUndo.column === key) {
          return true;
        }
        return Boolean((state.textWriteUndoStack[key] || []).length);
      }

      async function undoLastWrite() {
        ensureColumnDrafts();
        const column = readTextColumn();
        const draft = state.textColumnDrafts[column];
        let capture = null;
        let needsBackendUndo = false;
        if (state.pendingWriteUndo && state.pendingWriteUndo.column === column) {
          capture = state.pendingWriteUndo;
          state.pendingWriteUndo = null;
        } else {
          const stack = state.textWriteUndoStack[column] || [];
          if (!stack.length) {
            throw new Error('No write to undo in this column.');
          }
          capture = stack.pop();
          needsBackendUndo = true;
        }
        if (needsBackendUndo) {
          await apiRequest('/api/text/undo_last_write', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ column }),
          });
        }
        truncateTrailFromSegmentIndex(capture.trailStartIndex);
        draft.committed = capture.committedBefore;
        if (dom.textInput) {
          dom.textInput.value = capture.committedBefore;
        }
        persistCurrentColumnDraft();
        if (LiveVoiceController.isListening()) {
          LiveVoiceController._textareaBase = capture.committedBefore;
        }
        refreshUiState({ redrawBoard: true });
        pushFeed(`Undid last write in ${column} column.`, 'info');
      }

      function syncSketchAdvancedVisibility() {
        if (!dom.advancedSketchSettings) {
          return;
        }
        dom.advancedSketchSettings.hidden = !DEBUG_MODE;
        if (!DEBUG_MODE) {
          dom.advancedSketchSettings.open = false;
        }
      }

      function syncVectorizationMethodUi() {
        if (!dom.sketchVectorizationMethod || !dom.sketchPotraceHint) {
          return;
        }
        const method = dom.sketchVectorizationMethod.value;
        const autotraceSelected = method === 'autotrace';
        if (dom.autotraceSpeckleField) {
          dom.autotraceSpeckleField.hidden = !autotraceSelected;
          dom.autotraceSpeckleField.classList.toggle('field-disabled', !autotraceSelected);
        }
        if (dom.autotraceSpeckleReadout && dom.autotraceSpeckleStrength) {
          dom.autotraceSpeckleReadout.textContent = String(dom.autotraceSpeckleStrength.value);
        }
        if (method === 'autotrace' && state.autotraceAvailable === false) {
          dom.sketchPotraceHint.innerHTML = '<strong>AutoTrace not found on server.</strong> Run <code>scripts/install_autotrace.sh</code> or switch to Potrace.';
        } else if (method === 'potrace' && state.potraceAvailable === false) {
          dom.sketchPotraceHint.innerHTML = '<strong>Potrace not found on server.</strong> Install potrace (e.g. <code>apt install potrace</code>) or switch to AutoTrace.';
        } else if (method === 'autotrace') {
          dom.sketchPotraceHint.innerHTML = 'AutoTrace traces a single centerline through each stroke (<code>autotrace -centerline</code>). Recommended for robot drawing. For dense coloring-book art with many thin lines, <strong>Potrace</strong> often preserves more detail.';
        } else if (method === 'potrace') {
          dom.sketchPotraceHint.innerHTML = 'Potrace traces filled regions into SVG paths (outline/double-edge on some character art). Requires <code>potrace</code> on the server.';
        } else {
          dom.sketchPotraceHint.innerHTML = 'AutoTrace is the default vectorization engine for raster sketches.';
        }
      }

      syncSketchAdvancedVisibility();
      syncVectorizationMethodUi();

      function pipelineDisplayName(payloadOrMode) {
        const payload = typeof payloadOrMode === 'string' ? { pipeline_mode: payloadOrMode } : (payloadOrMode || {});
        const mode = String(payload.pipeline_mode || payload.source_type || '').toLowerCase();
        if (mode === 'sketch_autotrace') {
          return 'Centerline (AutoTrace)';
        }
        if (mode === 'sketch_potrace') {
          return 'Outline (Potrace)';
        }
        if (mode.startsWith('sketch_ai_photo_')) {
          const engine = mode.endsWith('_potrace') ? 'Potrace' : 'AutoTrace';
          return `AI Photo → ${engine}`;
        }
        if (mode.startsWith('sketch_ai_coloring_')) {
          const engine = mode.endsWith('_potrace') ? 'Potrace' : 'AutoTrace';
          return `AI Coloring Book → ${engine}`;
        }
        if (mode.startsWith('sketch_raw_print_')) {
          const engine = mode.endsWith('_potrace') ? 'Potrace' : 'AutoTrace';
          return `Raw Print → ${engine}`;
        }
        if (mode === 'sketch_image' || mode === 'sketch_centerline') {
          return 'Raster Sketch';
        }
        if (mode === 'svg_vector') {
          return 'SVG Vector';
        }
        if (mode === 'text_vector') {
          return 'Text Vector';
        }
        return String(payload.pipeline_mode || payload.source_type || 'Executable');
      }

      function escapeHtml(text) {
        return String(text)
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
      }

      function pushFeed(copy, kind = 'info') {
        const item = {
          copy,
          kind,
          time: new Date().toLocaleTimeString(),
        };
        state.feedItems.unshift(item);
        state.feedItems = state.feedItems.slice(0, 18);
        dom.activityFeed.innerHTML = state.feedItems.map((entry) => (
          `<div class="feed-item ${entry.kind}">` +
            `<div class="feed-time">${escapeHtml(entry.time)}</div>` +
            `<div class="feed-copy">${escapeHtml(entry.copy)}</div>` +
          `</div>`
        )).join('');
      }

      function setNotice(kind, title, copy) {
        dom.notice.className = `notice visible ${kind || ''}`.trim();
        dom.noticeTitle.textContent = title;
        dom.noticeCopy.textContent = copy;
      }

      function clearNotice() {
        dom.notice.className = 'notice';
        dom.noticeTitle.textContent = '';
        dom.noticeCopy.textContent = '';
      }

      function setStatusPill(element, textElement, tone, text) {
        element.className = `status-pill ${tone}`;
        textElement.textContent = text;
      }

      function safeNumber(value) {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : null;
      }

      function formatRange(min, max) {
        if (!Number.isFinite(min) || !Number.isFinite(max)) {
          return '--';
        }
        return `${min.toFixed(2)}..${max.toFixed(2)}`;
      }

      function formatPoint(x, y) {
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          return '--';
        }
        return `${x.toFixed(3)}, ${y.toFixed(3)}`;
      }

      function currentBoard() {
        return state.board || (state.runtime && state.runtime.board_info) || DEFAULT_BOARD;
      }

      function writableBounds() {
        const board = currentBoard();
        return {
          xMin: Number(board.writable_x_min),
          xMax: Number(board.writable_x_max),
          yMin: Number(board.writable_y_min),
          yMax: Number(board.writable_y_max),
        };
      }

      function safeBounds() {
        const board = currentBoard();
        return {
          xMin: Number.isFinite(Number(board.safe_x_min)) ? Number(board.safe_x_min) : Number(board.writable_x_min),
          xMax: Number.isFinite(Number(board.safe_x_max)) ? Number(board.safe_x_max) : Number(board.writable_x_max),
          yMin: Number.isFinite(Number(board.safe_y_min)) ? Number(board.safe_y_min) : Number(board.writable_y_min),
          yMax: Number.isFinite(Number(board.safe_y_max)) ? Number(board.safe_y_max) : Number(board.writable_y_max),
        };
      }

      function carriagePenBounds() {
        const board = currentBoard();
        const halfW = CARRIAGE.width * 0.5;
        const halfH = CARRIAGE.height * 0.5;
        return {
          xMin: halfW + CARRIAGE.penOffsetX,
          xMax: Number(board.width) - halfW + CARRIAGE.penOffsetX,
          yMin: halfH + CARRIAGE.penOffsetY,
          yMax: Number(board.height) - halfH + CARRIAGE.penOffsetY,
        };
      }

      function carriageSafeWritableBounds() {
        const writable = writableBounds();
        const pen = carriagePenBounds();
        return {
          xMin: Math.max(writable.xMin, pen.xMin),
          xMax: Math.min(writable.xMax, pen.xMax),
          yMin: Math.max(writable.yMin, pen.yMin),
          yMax: Math.min(writable.yMax, pen.yMax),
        };
      }

      function carriageSafeWorkspaceBounds() {
        const safe = safeBounds();
        const pen = carriagePenBounds();
        return {
          xMin: Math.max(safe.xMin, pen.xMin),
          xMax: Math.min(safe.xMax, pen.xMax),
          yMin: Math.max(safe.yMin, pen.yMin),
          yMax: Math.min(safe.yMax, pen.yMax),
        };
      }

      function sketchValidationBounds() {
        const writable = writableBounds();
        const workspace = carriageSafeWorkspaceBounds();
        return {
          xMin: Math.max(writable.xMin, workspace.xMin),
          xMax: Math.min(writable.xMax, workspace.xMax),
          yMin: Math.max(writable.yMin, workspace.yMin),
          yMax: Math.min(writable.yMax, workspace.yMax),
        };
      }

      function displayBounds() {
        return carriageSafeWritableBounds();
      }

      function textSafeBounds() {
        const safe = safeBounds();
        const carriageSafe = carriageSafeWritableBounds();
        return {
          xMin: Math.max(safe.xMin, carriageSafe.xMin),
          xMax: Math.min(safe.xMax, carriageSafe.xMax),
          yMin: Math.max(safe.yMin, carriageSafe.yMin),
          yMax: Math.min(safe.yMax, carriageSafe.yMax),
        };
      }

      function textColumnBounds() {
        const safe = textSafeBounds();
        const column = readTextColumn();
        if (column === 'full') {
          return { xMin: safe.xMin, xMax: safe.xMax, column };
        }
        const width = safe.xMax - safe.xMin;
        const third = width / 3.0;
        if (column === 'left') {
          return { xMin: safe.xMin, xMax: safe.xMin + third, column };
        }
        if (column === 'center') {
          return { xMin: safe.xMin + third, xMax: safe.xMin + (2 * third), column };
        }
        return { xMin: safe.xMin + (2 * third), xMax: safe.xMax, column };
      }

      function drawColumnGuides(layout) {
        if (state.activeTool !== 'text') {
          return;
        }
        const column = readTextColumn();
        if (column === 'full') {
          return;
        }
        const safe = textSafeBounds();
        const width = safe.xMax - safe.xMin;
        const third = width / 3.0;
        const dividers = [safe.xMin + third, safe.xMin + (2 * third)];
        const top = boardToCanvas(layout, safe.xMin, safe.yMin);
        const bottom = boardToCanvas(layout, safe.xMin, safe.yMax);
        ctx.save();
        ctx.strokeStyle = 'rgba(239, 123, 77, 0.55)';
        ctx.lineWidth = 1.6;
        ctx.setLineDash([8, 6]);
        dividers.forEach((xBoard) => {
          const point = boardToCanvas(layout, xBoard, safe.yMin);
          ctx.beginPath();
          ctx.moveTo(point.x, top.y);
          ctx.lineTo(point.x, bottom.y);
          ctx.stroke();
        });
        const active = textColumnBounds();
        const activeTopLeft = boardToCanvas(layout, active.xMin, safe.yMin);
        const activeBottomRight = boardToCanvas(layout, active.xMax, safe.yMax);
        roundedRect(
          ctx,
          activeTopLeft.x,
          activeTopLeft.y,
          activeBottomRight.x - activeTopLeft.x,
          activeBottomRight.y - activeTopLeft.y,
          10,
        );
        ctx.fillStyle = 'rgba(239, 123, 77, 0.06)';
        ctx.fill();
        ctx.setLineDash([]);
        ctx.restore();
      }

      function defaultPlacementForTool(tool) {
        const writable = writableBounds();
        if (tool === 'text') {
          const textSafe = textSafeBounds();
          return {
            x: textSafe.xMin + 0.02,
            y: textSafe.yMin + 0.02,
            scale: 1,
          };
        }
        const display = displayBounds();
        return {
          x: display.xMin + ((display.xMax - display.xMin) * 0.5),
          y: display.yMin + ((display.yMax - display.yMin) * 0.5),
          scale: 1,
        };
      }

      function syncPlacementLabels() {
        if (state.activeTool === 'text') {
          dom.placementXLabel.textContent = 'Start X (m)';
          dom.placementYLabel.textContent = 'Start Y (m)';
          dom.placementCopy.textContent = 'For text, X and Y define the top-left writing origin inside the carriage-safe writing region.';
          dom.placementHelper.textContent = 'Text preview and draw validate against the writable region that also keeps the carriage body inside the board.';
        } else {
          dom.placementXLabel.textContent = 'Center X (m)';
          dom.placementYLabel.textContent = 'Center Y (m)';
          dom.placementCopy.textContent = 'For picture uploads and SVG markup, X and Y define the board-space center used for fitted vector placement.';
          dom.placementHelper.textContent = 'Executable previews show whether geometry stays inside the writable and safe workspace before drawing.';
        }
      }

      function setPlacementInputs(placement) {
        dom.placementX.value = Number(placement.x).toFixed(3);
        dom.placementY.value = Number(placement.y).toFixed(3);
        dom.placementScale.value = Number(placement.scale).toFixed(2);
      }

      function syncPlacementDefaults(force = false) {
        if (!force && state.placementTouched) {
          return;
        }
        setPlacementInputs(defaultPlacementForTool(state.activeTool));
        if (force) {
          state.placementTouched = false;
        }
      }

      function readPlacement() {
        const x = Number(dom.placementX.value);
        const y = Number(dom.placementY.value);
        const scale = Number(dom.placementScale.value);
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(scale)) {
          throw new Error('Placement fields must contain valid numbers.');
        }
        if (scale <= 0) {
          throw new Error('Placement scale must be greater than zero.');
        }
        return { x, y, scale };
      }

      function setTextFontSource(value) {
        const normalized = typeof value === 'string' ? value.trim().toLowerCase() : 'relief_singleline';
        const allowed = new Set(['relief_singleline', 'hershey_sans_1', 'dejavu_sans']);
        dom.textFontSource.value = allowed.has(normalized) ? normalized : 'relief_singleline';
      }

      function readTextOptions() {
        return {
          font_source: typeof dom.textFontSource.value === 'string'
            ? dom.textFontSource.value.trim().toLowerCase()
            : 'relief_singleline',
          line_height: Number(dom.textLineHeight ? dom.textLineHeight.value : 1.75),
          column_seed_gap: Number(dom.textColumnSeedGap ? dom.textColumnSeedGap.value : 1.75),
          text_column: readTextColumn(),
        };
      }

      function readTextColumn() {
        const active = dom.textColumnButtons.find((button) => button.classList.contains('active'));
        const column = active ? String(active.dataset.textColumn || 'full').toLowerCase() : 'full';
        if (column === 'left' || column === 'center' || column === 'right') {
          return column;
        }
        return 'full';
      }

      function syncTextLineHeightReadout() {
        if (!dom.textLineHeightReadout || !dom.textLineHeight) {
          return;
        }
        const lineHeight = Number(dom.textLineHeight.value);
        const board = currentBoard();
        const glyphHeight = Number(board.glyph_height) || 0.086;
        const scale = Number(dom.placementScale ? dom.placementScale.value : 1) || 1;
        const rowClearanceM = Math.max(0, lineHeight - 1) * glyphHeight * scale;
        const clearanceCm = Number.isFinite(rowClearanceM) ? (rowClearanceM * 100).toFixed(1) : '--';
        dom.textLineHeightReadout.textContent = `${lineHeight.toFixed(2)} (~${clearanceCm} cm between rows)`;
      }

      function syncTextColumnSeedGapReadout() {
        if (!dom.textColumnSeedGapReadout || !dom.textColumnSeedGap) {
          return;
        }
        const seedGap = Number(dom.textColumnSeedGap.value);
        const board = currentBoard();
        const glyphHeight = Number(board.glyph_height) || 0.086;
        const scale = Number(dom.placementScale ? dom.placementScale.value : 1) || 1;
        const gapM = seedGap * glyphHeight * scale;
        const gapCm = Number.isFinite(gapM) ? (gapM * 100).toFixed(1) : '--';
        dom.textColumnSeedGapReadout.textContent = `${seedGap.toFixed(2)} (~${gapCm} cm below ink)`;
      }

      function committedPrefix(column) {
        ensureColumnDrafts();
        const key = column || readTextColumn();
        const draft = state.textColumnDrafts[key];
        return draft && draft.committed ? draft.committed : '';
      }

      function enforceCommittedTextGuard() {
        if (!dom.textInput) {
          return;
        }
        const committed = committedPrefix();
        if (!committed) {
          return;
        }
        const value = dom.textInput.value;
        if (value.startsWith(committed)) {
          const minCaret = committed.length;
          const start = dom.textInput.selectionStart;
          const end = dom.textInput.selectionEnd;
          if (start < minCaret || end < minCaret) {
            dom.textInput.setSelectionRange(
              Math.max(start, minCaret),
              Math.max(end, minCaret),
            );
          }
          return;
        }
        const suffix = value.length > committed.length ? value.slice(committed.length) : '';
        dom.textInput.value = committed + suffix;
        dom.textInput.setSelectionRange(dom.textInput.value.length, dom.textInput.value.length);
        syncActiveColumnDraftFromTextarea();
      }

      function resolveTextWritePayload() {
        ensureColumnDrafts();
        const column = readTextColumn();
        const draft = state.textColumnDrafts[column];
        const fullText = dom.textInput ? dom.textInput.value : '';
        if (!fullText.trim()) {
          throw new Error('Enter or dictate text before writing.');
        }
        const committed = draft.committed || '';
        if (fullText === committed) {
          throw new Error('No new text to write.');
        }
        if (!committed) {
          return { text: fullText, resetCursor: true, mode: 'full', column };
        }
        if (fullText.startsWith(committed)) {
          const suffix = fullText.slice(committed.length);
          if (!suffix.trim()) {
            throw new Error('No new text to write.');
          }
          return { text: suffix, resetCursor: false, mode: 'append', column };
        }
        throw new Error('Text was edited — press Clear Local Trail to start fresh.');
      }

      async function resetBackendTextCursor(column, { clearInk = true } = {}) {
        const options = { method: 'POST' };
        if (column || !clearInk) {
          options.headers = { 'Content-Type': 'application/json' };
          options.body = JSON.stringify({
            ...(column ? { column } : {}),
            clear_ink: clearInk,
          });
        }
        await apiRequest('/api/text/reset_cursor', options);
      }

      function parseJsonResponse(response) {
        return response.text().then((text) => {
          if (!text) {
            return {};
          }
          try {
            return JSON.parse(text);
          } catch (_error) {
            return { detail: text };
          }
        });
      }

      async function apiRequest(url, options = {}) {
        const timeoutMs = options.timeoutMs == null ? 120000 : Number(options.timeoutMs);
        const { timeoutMs: _ignored, ...fetchOptions } = options;
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), timeoutMs);
        try {
          const response = await fetch(url, { ...fetchOptions, signal: controller.signal });
          const payload = await parseJsonResponse(response);
          if (!response.ok) {
            throw new Error(payload && payload.detail ? payload.detail : `${response.status} ${response.statusText}`);
          }
          return payload;
        } catch (error) {
          if (error && error.name === 'AbortError') {
            throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
          }
          throw error;
        } finally {
          window.clearTimeout(timer);
        }
      }

      function prettyJson(value) {
        return JSON.stringify(value, null, 2);
      }

      async function refreshRuntime() {
        const [runtime, health] = await Promise.all([
          apiRequest('/api/runtime'),
          apiRequest('/api/health').catch(() => null),
        ]);
        state.runtime = runtime;
        state.potraceAvailable = health && health.potrace_available === true ? true : (health ? false : null);
        state.autotraceAvailable = health && health.autotrace_available === true ? true : (health ? false : null);
        state.aiPreprocessAvailable = health && health.ai_preprocess_available === true ? true : (health ? false : null);
        state.cudaAvailable = health && health.cuda_available === true ? true : (health ? false : null);
        state.anilinesWeightsCached = health && health.anilines_weights_cached === true ? true : (health ? false : null);
        syncVectorizationMethodUi();
        syncImagePreprocessUi();
        syncBoardFabVectorMethodUi();
        state.manualPenMode = runtime.manual_pen_mode || 'auto';
        state.board = runtime.board_info || state.board;

        const backendTone = runtime.ready ? 'ready' : 'connecting';
        setStatusPill(dom.backendPill, dom.backendText, backendTone, runtime.ready ? 'Backend · ready' : 'Backend · waiting');
        setStatusPill(
          dom.runtimePill,
          dom.runtimeText,
          runtime.ready ? 'ready' : 'connecting',
          runtime.ready ? `Runtime · ${runtime.active_mode || 'ready'}` : 'Runtime · waiting'
        );
        if (DEBUG_MODE) {
          await refreshDebugPanels();
        }
        refreshUiState({ redrawBoard: true });
      }

      async function refreshDebugPanels() {
        if (!DEBUG_MODE) {
          return;
        }
        const [planDebug, executionDebug, curveFitDebug] = await Promise.all([
          apiRequest('/api/debug/last-plan'),
          apiRequest('/api/debug/last-execution'),
          apiRequest('/api/debug/last-curve-fit'),
        ]);
        state.debugPlan = planDebug;
        state.debugExecution = executionDebug;
        state.debugCurveFit = curveFitDebug;
        renderDebugPanels();
      }

      function renderDebugPanels() {
        const planDebug = state.debugPlan;
        const executionDebug = state.debugExecution;

        if (!planDebug || !planDebug.available) {
          dom.debugPlanPanel.textContent = 'No plan diagnostics yet.';
          dom.debugSamplingPanel.textContent = 'No sampling diagnostics yet.';
        } else {
          const routeMetadata = planDebug.route_metadata || null;
          dom.debugPlanPanel.textContent = prettyJson({
            source_type: planDebug.source_type,
            route: routeMetadata && routeMetadata.route ? routeMetadata.route : null,
            trace_mode: routeMetadata && routeMetadata.mode ? routeMetadata.mode : null,
            primitive_counts: planDebug.plan ? planDebug.plan.primitive_counts : null,
            sampled_bounds: planDebug.plan ? planDebug.plan.sampled_bounds : null,
            optimizer_stats: planDebug.optimizer_stats || null,
            route_metadata: routeMetadata,
          });
          dom.debugSamplingPanel.textContent = prettyJson({
            preview_sampling: planDebug.preview_sampling || null,
            runtime_sampling: planDebug.runtime_sampling || null,
            parity: planDebug.parity || null,
            point_budget: planDebug.point_budget || null,
            timings_ms: planDebug.timings_ms || null,
          });
        }

        if (!executionDebug || !executionDebug.available) {
          dom.debugExecutionPanel.textContent = 'No execution diagnostics yet.';
        } else {
          const executorDebug = executionDebug.executor || null;
          dom.debugExecutionPanel.textContent = prettyJson({
            source_type: executionDebug.source_type,
            chosen_transport: executionDebug.chosen_transport,
            published_transports: executionDebug.published_transports,
            transport_topics: executionDebug.transport_topics,
            chunk_count: executorDebug ? executorDebug.chunk_count || null : null,
            primitive_count: executorDebug ? executorDebug.primitive_count || null : null,
            sampled_point_count: executorDebug ? executorDebug.sampled_point_count || null : null,
            schedule_count: executorDebug ? executorDebug.schedule_count || null : null,
            timings_ms: executionDebug.timings_ms || null,
            executor: executorDebug,
          });
        }

        const curveFitDebug = state.debugCurveFit;
        if (!curveFitDebug || !curveFitDebug.available) {
          dom.debugCurveFitPanel.textContent = 'No curve-fit diagnostics yet.';
        } else {
          dom.debugCurveFitPanel.textContent = prettyJson({
            source_type: curveFitDebug.source_type,
            route: curveFitDebug.route || null,
            trace_mode: curveFitDebug.trace_mode || null,
            image_size: curveFitDebug.image_size || null,
            raw_contour_count: curveFitDebug.raw_contour_count,
            span_count: curveFitDebug.span_count,
            merge_stats: curveFitDebug.merge_stats || null,
            fit_summary: curveFitDebug.fit_summary || null,
            fit_tolerances: curveFitDebug.fit_tolerances || null,
            worst_spans: curveFitDebug.worst_spans || null,
          });
        }
      }

      async function setMode(mode) {
        const previousRuntime = state.runtime ? { ...state.runtime } : null;
        if (state.runtime) {
          state.runtime = { ...state.runtime, active_mode: mode };
          refreshUiState();
        }
        try {
          const payload = await apiRequest('/api/mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
          });
          state.runtime = payload.runtime;
          state.manualPenMode = payload.runtime && payload.runtime.manual_pen_mode
            ? payload.runtime.manual_pen_mode
            : 'auto';
          refreshUiState();
          pushFeed(`Mode switched to ${mode}.`, 'success');
          return payload.runtime;
        } catch (error) {
          if (previousRuntime) {
            state.runtime = previousRuntime;
            refreshUiState();
          }
          throw error;
        }
      }

      async function setManualPenMode(mode) {
        const payload = await apiRequest('/api/manual/pen', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode }),
        });
        state.runtime = payload.runtime;
        state.manualPenMode = payload.runtime && payload.runtime.manual_pen_mode ? payload.runtime.manual_pen_mode : mode;
        refreshUiState();
        const label = mode === 'down' ? 'press' : mode === 'up' ? 'release' : 'auto';
        pushFeed(`Arm test set to ${label}.`, 'success');
        return payload.runtime;
      }

      async function ensureMode(mode) {
        if (state.runtime && state.runtime.active_mode === mode) {
          return;
        }
        await setMode(mode);
      }

      function previewStrokeColor() {
        const sketchBlack = 'rgba(17, 24, 39, 0.92)';
        if (!state.vectorPreview) {
          return sketchBlack;
        }
        if (state.vectorPreview.sourceType === 'sketch_image' || state.vectorPreview.sourceType === 'sketch_centerline') {
          return sketchBlack;
        }
        if (state.vectorPreview.sourceType === 'svg') {
          return sketchBlack;
        }
        if (state.vectorPreview.sourceType === 'image') {
          return sketchBlack;
        }
        return 'rgba(37, 99, 235, 0.96)';
      }

      function clonePlacement(placement) {
        if (!placement) {
          return null;
        }
        return {
          x: Number(placement.x),
          y: Number(placement.y),
          scale: Number(placement.scale),
        };
      }

      function cloneBounds(bounds) {
        if (!bounds) {
          return null;
        }
        const valueOrFallback = (value, fallback) => (
          value === null || value === undefined ? fallback : value
        );
        return {
          xMin: Number(valueOrFallback(bounds.x_min, bounds.xMin)),
          xMax: Number(valueOrFallback(bounds.x_max, bounds.xMax)),
          yMin: Number(valueOrFallback(bounds.y_min, bounds.yMin)),
          yMax: Number(valueOrFallback(bounds.y_max, bounds.yMax)),
        };
      }

      function activeBoardBrushRadiusM(session = state.boardRasterSession) {
        if (state.boardEditMode === 'eraser') {
          return Number(session?.eraserTipRadiusM ?? state.eraserTipRadiusM ?? 0.005);
        }
        return Number(session?.penTipRadiusM ?? state.penTipRadiusM ?? 0.003);
      }

      function clearSketchBoardOverlay() {
        state.sketchBoardOverlayToken += 1;
        if (state.sketchBoardOverlayImage) {
          state.sketchBoardOverlayImage.onload = null;
          state.sketchBoardOverlayImage.onerror = null;
        }
        state.sketchBoardOverlayImage = null;
        state.sketchBoardOverlayLoaded = false;
        if (state.sketchBoardOverlayObjectUrl) {
          URL.revokeObjectURL(state.sketchBoardOverlayObjectUrl);
          state.sketchBoardOverlayObjectUrl = null;
        }
      }

      function svgTextForBoardOverlay(svgText) {
        return String(svgText || '').replace(
          /<rect[^>]*\bfill\s*=\s*["']white["'][^>]*\/?>/gi,
          '',
        );
      }

      function sketchBoardOverlayActive() {
        return Boolean(
          state.vectorPreview &&
          state.currentExecutionPreviewSvg &&
          state.sketchPreviewUrl &&
          state.sketchBoardOverlayImage &&
          state.sketchBoardOverlayLoaded
        );
      }

      function prepareSketchBoardOverlay() {
        const overlayText = svgTextForBoardOverlay(state.sketchPreviewSvgText);
        if (!overlayText.trim()) {
          clearSketchBoardOverlay();
          return;
        }
        const token = state.sketchBoardOverlayToken + 1;
        state.sketchBoardOverlayToken = token;
        const previousImage = state.sketchBoardOverlayImage;
        const previousUrl = state.sketchBoardOverlayObjectUrl;
        const previousLoaded = state.sketchBoardOverlayLoaded;
        const image = new Image();
        const overlayBlob = new Blob([overlayText], { type: 'image/svg+xml' });
        const newUrl = URL.createObjectURL(overlayBlob);
        image.onload = () => {
          if (state.sketchBoardOverlayToken !== token) {
            URL.revokeObjectURL(newUrl);
            return;
          }
          if (previousImage && previousImage !== image) {
            previousImage.onload = null;
            previousImage.onerror = null;
          }
          if (previousUrl && previousUrl !== newUrl) {
            URL.revokeObjectURL(previousUrl);
          }
          state.sketchBoardOverlayImage = image;
          state.sketchBoardOverlayObjectUrl = newUrl;
          state.sketchBoardOverlayLoaded = true;
          updateSketchPreviewDiagnostics();
          refreshUiState({ redrawBoard: true });
        };
        image.onerror = () => {
          if (state.sketchBoardOverlayToken !== token) {
            URL.revokeObjectURL(newUrl);
            return;
          }
          URL.revokeObjectURL(newUrl);
          state.sketchBoardOverlayImage = previousImage;
          state.sketchBoardOverlayObjectUrl = previousUrl;
          state.sketchBoardOverlayLoaded = previousLoaded;
          updateSketchPreviewDiagnostics();
          refreshUiState({ redrawBoard: true });
        };
        image.src = newUrl;
      }

      function revokePreviewResources(previewState) {
        clearSketchBoardOverlay();
        state.sketchPreviewPayload = null;
        if (!previewState || !previewState.rasterOverlay) {
          if (state.sketchPreviewUrl) {
            URL.revokeObjectURL(state.sketchPreviewUrl);
            state.sketchPreviewUrl = null;
          }
          state.sketchPreviewSvgText = '';
          return;
        }
        const imageUrl = previewState.rasterOverlay.imageUrl;
        if (typeof imageUrl === 'string' && imageUrl.startsWith('blob:')) {
          URL.revokeObjectURL(imageUrl);
        }
        if (state.sketchPreviewUrl) {
          URL.revokeObjectURL(state.sketchPreviewUrl);
          state.sketchPreviewUrl = null;
        }
        state.sketchPreviewSvgText = '';
      }

      function setSketchDrawStatus(message) {
        if (dom.sketchDrawStatus) {
          dom.sketchDrawStatus.textContent = message;
        }
      }

      function resetSketchDrawState(message = 'Draw is available after Generate Preview returns a valid preview_id.') {
        state.sketchPreviewId = null;
        state.sketchDrawBusy = false;
        setSketchDrawStatus(message);
      }

      function previewBaseBounds(previewState = state.vectorPreview) {
        if (!previewState) {
          return null;
        }
        if (previewState.preview && previewState.preview.bounds) {
          return cloneBounds(previewState.preview.bounds);
        }
        if (previewState.rasterOverlay && previewState.rasterOverlay.bounds) {
          return cloneBounds(previewState.rasterOverlay.bounds);
        }
        return null;
      }

      function previewIsBoardVisible(previewState = state.vectorPreview) {
        return Boolean(previewState && previewState.boardVisible && previewBaseBounds(previewState));
      }

      function previewDisplayPlacement(previewState = state.vectorPreview) {
        if (!previewState) {
          return null;
        }
        return clonePlacement(previewState.displayPlacement || previewState.basePlacement);
      }

      function transformedPreviewBounds(previewState = state.vectorPreview) {
        const baseBounds = previewBaseBounds(previewState);
        if (!previewState || !baseBounds) {
          return null;
        }
        const basePlacement = clonePlacement(previewState.basePlacement);
        const displayPlacement = clonePlacement(previewState.displayPlacement || previewState.basePlacement);
        if (!basePlacement || !displayPlacement || basePlacement.scale <= 0) {
          return null;
        }
        const xMin = Number(baseBounds.xMin);
        const xMax = Number(baseBounds.xMax);
        const yMin = Number(baseBounds.yMin);
        const yMax = Number(baseBounds.yMax);
        if (![xMin, xMax, yMin, yMax].every(Number.isFinite)) {
          return null;
        }
        const scaleRatio = displayPlacement.scale / basePlacement.scale;
        const transform = (x, y) => ({
          x: displayPlacement.x + ((x - basePlacement.x) * scaleRatio),
          y: displayPlacement.y + ((y - basePlacement.y) * scaleRatio),
        });
        const corners = [
          transform(xMin, yMin),
          transform(xMax, yMin),
          transform(xMax, yMax),
          transform(xMin, yMax),
        ];
        return {
          xMin: Math.min(...corners.map((point) => point.x)),
          xMax: Math.max(...corners.map((point) => point.x)),
          yMin: Math.min(...corners.map((point) => point.y)),
          yMax: Math.max(...corners.map((point) => point.y)),
        };
      }

      function transformedPreviewStrokes(previewState = state.vectorPreview) {
        if (!previewState || !previewState.preview || !Array.isArray(previewState.preview.strokes)) {
          return [];
        }
        const basePlacement = clonePlacement(previewState.basePlacement);
        const displayPlacement = clonePlacement(previewState.displayPlacement || previewState.basePlacement);
        if (!basePlacement || !displayPlacement || basePlacement.scale <= 0) {
          return previewState.preview.strokes;
        }
        const scaleRatio = displayPlacement.scale / basePlacement.scale;
        return previewState.preview.strokes.map((stroke) => (
          Array.isArray(stroke)
            ? stroke.map((point) => [
              displayPlacement.x + ((Number(point[0]) - basePlacement.x) * scaleRatio),
              displayPlacement.y + ((Number(point[1]) - basePlacement.y) * scaleRatio),
            ])
            : stroke
        ));
      }

      function syncPreviewPlacementInputs(placement) {
        if (!placement) {
          return;
        }
        setPlacementInputs(placement);
        state.placementTouched = true;
      }

      function previewPlacementBounds() {
        const fitSafe = Boolean(dom.sketchFitSafe?.checked);
        if (fitSafe && state.board?.safe_bounds) {
          return state.board.safe_bounds;
        }
        if (state.board?.bounds) {
          return state.board.bounds;
        }
        return null;
      }

      function clampPreviewPlacement(placement, previewState = state.vectorPreview) {
        if (!placement || !previewState) {
          return placement;
        }
        const bounds = previewPlacementBounds();
        const transformed = transformedPreviewBounds({
          ...previewState,
          displayPlacement: placement,
        });
        if (!bounds || !transformed) {
          return placement;
        }
        const dxValues = [];
        const dyValues = [];
        if (transformed.xMin < bounds.x_min) {
          dxValues.push(bounds.x_min - transformed.xMin);
        }
        if (transformed.xMax > bounds.x_max) {
          dxValues.push(bounds.x_max - transformed.xMax);
        }
        if (transformed.yMin < bounds.y_min) {
          dyValues.push(bounds.y_min - transformed.yMin);
        }
        if (transformed.yMax > bounds.y_max) {
          dyValues.push(bounds.y_max - transformed.yMax);
        }
        const dx = dxValues.length ? Math.max(...dxValues) : 0;
        const dy = dyValues.length ? Math.max(...dyValues) : 0;
        return {
          x: Number(placement.x) + dx,
          y: Number(placement.y) + dy,
          scale: Number(placement.scale),
        };
      }

      function updatePreviewPlacement(nextPlacement, { syncInputs = true } = {}) {
        if (!state.vectorPreview || !nextPlacement) {
          return;
        }
        const clamped = clampPreviewPlacement(clonePlacement(nextPlacement));
        const cloned = clonePlacement(clamped);
        state.vectorPreview.displayPlacement = cloned;
        if (state.vectorPreview.drawRequest) {
          state.vectorPreview.drawRequest = {
            ...state.vectorPreview.drawRequest,
            placement: clonePlacement(cloned),
          };
        }
        if (syncInputs) {
          syncPreviewPlacementInputs(cloned);
        }
      }

      function penLineWidthPx(layout) {
        const radius = Number(state.penTipRadiusM || 0.003);
        return Math.max(1, (2 * radius) * Number(layout?.scale || 1));
      }

      function boardFabBoundsFromRect(rect) {
        const normalized = BoardFab.normalizeBoardRect ? BoardFab.normalizeBoardRect(rect) : null;
        if (!normalized) {
          return null;
        }
        return {
          x_min: normalized.xMin,
          x_max: normalized.xMax,
          y_min: normalized.yMin,
          y_max: normalized.yMax,
        };
      }

      function boardSafeClampBounds() {
        const safe = sketchValidationBounds();
        return {
          x_min: safe.xMin,
          x_max: safe.xMax,
          y_min: safe.yMin,
          y_max: safe.yMax,
        };
      }

      function imageBoundsInsideSketchValidation(imageBoundsBoard) {
        const rect = BoardFab.normalizeBoardRect
          ? BoardFab.normalizeBoardRect(imageBoundsBoard)
          : imageBoundsBoard;
        if (!rect) {
          return false;
        }
        const bounds = sketchValidationBounds();
        const eps = 1.0e-7;
        return (
          rect.xMin >= bounds.xMin - eps
          && rect.yMin >= bounds.yMin - eps
          && rect.xMax <= bounds.xMax + eps
          && rect.yMax <= bounds.yMax + eps
        );
      }

      function inkBoardBoundsFromSession(session) {
        if (!session?.memoryCanvas || !session.imageBoundsBoard || !BoardFab.inkBoundsPixelRect) {
          return null;
        }
        const inkPixel = BoardFab.inkBoundsPixelRect(
          session.memoryCanvas,
          session.cropNormalized || defaultCropNormalized(),
        );
        if (!inkPixel || !BoardFab.inkBoundsBoardRect) {
          return null;
        }
        return BoardFab.inkBoundsBoardRect(
          session.imageBoundsBoard,
          inkPixel,
          session.memoryCanvas.width,
          session.memoryCanvas.height,
          session.cropNormalized || defaultCropNormalized(),
        );
      }

      function inkBoundsInsideSketchValidation(session) {
        const inkBoard = inkBoardBoundsFromSession(session);
        if (!inkBoard) {
          return false;
        }
        return imageBoundsInsideSketchValidation(inkBoard);
      }

      function defaultCropNormalized() {
        return { xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
      }

      function clampSessionImageBounds(session) {
        if (!session?.imageBoundsBoard || !BoardFab.clampRectToBounds) {
          return;
        }
        const clamped = BoardFab.clampRectToBounds(session.imageBoundsBoard, boardSafeClampBounds());
        if (clamped) {
          session.imageBoundsBoard = clamped;
        }
      }

      function rasterFullBoundsBoard(session) {
        if (!session?.memoryCanvas || !session.metersPerPixelX || !session.metersPerPixelY) {
          return null;
        }
        const widthM = session.memoryCanvas.width * session.metersPerPixelX;
        const heightM = session.memoryCanvas.height * session.metersPerPixelY;
        const bounds = session.imageBoundsBoard;
        const centerX = bounds ? (bounds.xMin + bounds.xMax) * 0.5 : (boardSafeClampBounds().x_min + boardSafeClampBounds().x_max) * 0.5;
        const centerY = bounds ? (bounds.yMin + bounds.yMax) * 0.5 : (boardSafeClampBounds().y_min + boardSafeClampBounds().y_max) * 0.5;
        return BoardFab.clampRectToBounds({
          xMin: centerX - (widthM * 0.5),
          xMax: centerX + (widthM * 0.5),
          yMin: centerY - (heightM * 0.5),
          yMax: centerY + (heightM * 0.5),
        }, boardSafeClampBounds());
      }

      function applyRasterCropFromBoardRect(session) {
        if (!session?.imageBoundsBoard || !session.cropRectBoard) {
          return false;
        }
        const prevBounds = BoardFab.normalizeBoardRect
          ? BoardFab.normalizeBoardRect(session.imageBoundsBoard)
          : session.imageBoundsBoard;
        const cropRect = BoardFab.normalizeBoardRect
          ? BoardFab.normalizeBoardRect(session.cropRectBoard)
          : session.cropRectBoard;
        if (!prevBounds || !cropRect) {
          return false;
        }
        const prevWidth = prevBounds.xMax - prevBounds.xMin;
        const prevHeight = prevBounds.yMax - prevBounds.yMin;
        if (prevWidth <= 0 || prevHeight <= 0) {
          return false;
        }
        const parent = session.cropNormalized || defaultCropNormalized();
        const parentWidth = parent.xMax - parent.xMin;
        const parentHeight = parent.yMax - parent.yMin;
        const fracXMin = (cropRect.xMin - prevBounds.xMin) / prevWidth;
        const fracXMax = (cropRect.xMax - prevBounds.xMin) / prevWidth;
        const fracYMin = (cropRect.yMin - prevBounds.yMin) / prevHeight;
        const fracYMax = (cropRect.yMax - prevBounds.yMin) / prevHeight;
        const nextCrop = {
          xMin: Math.max(0, Math.min(1, parent.xMin + (fracXMin * parentWidth))),
          xMax: Math.max(0, Math.min(1, parent.xMin + (fracXMax * parentWidth))),
          yMin: Math.max(0, Math.min(1, parent.yMin + (fracYMin * parentHeight))),
          yMax: Math.max(0, Math.min(1, parent.yMin + (fracYMax * parentHeight))),
        };
        if ((nextCrop.xMax - nextCrop.xMin) < 0.001 || (nextCrop.yMax - nextCrop.yMin) < 0.001) {
          return false;
        }
        session.cropNormalized = nextCrop;
        const clamped = BoardFab.clampRectToBounds
          ? BoardFab.clampRectToBounds(cropRect, boardSafeClampBounds())
          : cropRect;
        session.imageBoundsBoard = clamped ? { ...clamped } : { ...cropRect };
        session.cropRectBoard = { ...session.imageBoundsBoard };
        session.dirty = true;
        return true;
      }

      function exportRasterSessionCanvas(session) {
        const crop = session.cropNormalized || defaultCropNormalized();
        const pixelRect = BoardFab.imageCropToPixelRect
          ? BoardFab.imageCropToPixelRect(
            crop,
            session.memoryCanvas.width,
            session.memoryCanvas.height,
          )
          : null;
        if (!pixelRect) {
          return session.memoryCanvas;
        }
        const exported = document.createElement('canvas');
        exported.width = pixelRect.sw;
        exported.height = pixelRect.sh;
        const exportCtx = exported.getContext('2d');
        exportCtx.fillStyle = '#ffffff';
        exportCtx.fillRect(0, 0, exported.width, exported.height);
        exportCtx.imageSmoothingEnabled = true;
        exportCtx.drawImage(
          session.memoryCanvas,
          pixelRect.sx,
          pixelRect.sy,
          pixelRect.sw,
          pixelRect.sh,
          0,
          0,
          pixelRect.sw,
          pixelRect.sh,
        );
        return exported;
      }

      function boardDragClampBounds() {
        if (state.board?.bounds) {
          return state.board.bounds;
        }
        const writable = writableBounds();
        return {
          x_min: writable.xMin,
          x_max: writable.xMax,
          y_min: writable.yMin,
          y_max: writable.yMax,
        };
      }

      function isRasterEditActive() {
        return Boolean(state.boardRasterSession?.active);
      }

      function isRasterEditing() {
        const session = state.boardRasterSession;
        return Boolean(session?.active && session.phase === 'edit');
      }

      function isRasterSessionPreviewReady() {
        const session = state.boardRasterSession;
        return Boolean(session?.active && session.phase === 'preview_ready');
      }

      function previewBoardLineWidth(layout) {
        return Math.max(1.4, Math.min(3.1, layout.scale * 0.011));
      }

      function clearBoardRasterSession() {
        state.boardRasterSession = null;
        state.boardEditMode = null;
        state.boardOverlayMode = null;
        state.boardEditLastPoint = null;
        state.boardEditSmoothPoint = null;
        state.boardEditToolPointer = null;
        if (dom.boardEditCanvas) {
          dom.boardEditCanvas.classList.remove('active');
          dom.boardEditCanvas.style.display = 'none';
          dom.boardEditCanvas.style.left = '';
          dom.boardEditCanvas.style.top = '';
          dom.boardEditCanvas.style.width = '';
          dom.boardEditCanvas.style.height = '';
          if (editCtx) {
            editCtx.clearRect(0, 0, dom.boardEditCanvas.width, dom.boardEditCanvas.height);
          }
        }
        if (dom.boardFabConfirm) {
          dom.boardFabConfirm.classList.remove('ready');
        }
        syncBoardFabState();
      }

      async function loadImageToCanvas(fileOrUrl) {
        const image = new Image();
        let objectUrl = null;
        if (fileOrUrl instanceof Blob || fileOrUrl instanceof File) {
          objectUrl = URL.createObjectURL(fileOrUrl);
          image.src = objectUrl;
        } else {
          image.src = String(fileOrUrl);
        }
        await new Promise((resolve, reject) => {
          image.onload = resolve;
          image.onerror = reject;
        });
        if (objectUrl) {
          URL.revokeObjectURL(objectUrl);
        }
        const canvas = document.createElement('canvas');
        canvas.width = image.naturalWidth || image.width;
        canvas.height = image.naturalHeight || image.height;
        canvas.getContext('2d').drawImage(image, 0, 0);
        return canvas;
      }

      function startRasterSessionFromCanvas(canvas, { source = 'file', mode = null, fixedBounds = false } = {}) {
        const memoryCanvas = document.createElement('canvas');
        memoryCanvas.width = canvas.width;
        memoryCanvas.height = canvas.height;
        memoryCanvas.getContext('2d').drawImage(canvas, 0, 0);
        let imageBoundsBoard;
        if (fixedBounds) {
          const bounds = sketchValidationBounds();
          imageBoundsBoard = {
            xMin: bounds.xMin,
            xMax: bounds.xMax,
            yMin: bounds.yMin,
            yMax: bounds.yMax,
          };
        } else {
          const safeBounds = boardSafeClampBounds();
          imageBoundsBoard = BoardFab.fitImageBoundsToBoard
            ? BoardFab.fitImageBoundsToBoard(
              memoryCanvas.width,
              memoryCanvas.height,
              safeBounds,
              BOARD_RASTER_FIT_MARGIN_M,
            )
            : null;
        }
        const cropNormalized = defaultCropNormalized();
        const metersPerPixelX = imageBoundsBoard
          ? (imageBoundsBoard.xMax - imageBoundsBoard.xMin) / memoryCanvas.width
          : null;
        const metersPerPixelY = imageBoundsBoard
          ? (imageBoundsBoard.yMax - imageBoundsBoard.yMin) / memoryCanvas.height
          : null;
        clearSketchPreviewForBoardEdit();
        state.boardRasterSession = {
          active: true,
          phase: 'edit',
          source,
          mode: mode || readImagePreprocessMode(),
          memoryCanvas,
          cropNormalized,
          metersPerPixelX,
          metersPerPixelY,
          imageBoundsBoard,
          cropRectBoard: imageBoundsBoard ? { ...imageBoundsBoard } : null,
          cropInteraction: null,
          moveInteraction: null,
          dirty: false,
          fixedBounds: Boolean(fixedBounds),
          penTipRadiusM: state.penTipRadiusM,
          eraserTipRadiusM: state.eraserTipRadiusM,
          vectorizationMethod: String(readFilePreviewSettings().vectorization_method || 'autotrace'),
        };
        state.boardEditMode = null;
        state.boardOverlayMode = null;
        state.boardEditLastPoint = null;
        if (dom.boardFabConfirm) {
          dom.boardFabConfirm.classList.remove('ready');
        }
        syncBoardFabState();
        refreshUiState({ redrawBoard: true });
      }

      function blankCanvasMemorySize() {
        const bounds = sketchValidationBounds();
        const widthM = Math.max(0.01, bounds.xMax - bounds.xMin);
        const heightM = Math.max(0.01, bounds.yMax - bounds.yMin);
        const aspect = widthM / heightM;
        const base = 2048;
        if (aspect >= 1) {
          return { width: base, height: Math.max(256, Math.round(base / aspect)) };
        }
        return { width: Math.max(256, Math.round(base * aspect)), height: base };
      }

      function startFixedBlankCanvasSession() {
        const { width, height } = blankCanvasMemorySize();
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const blankCtx = canvas.getContext('2d');
        blankCtx.fillStyle = '#ffffff';
        blankCtx.fillRect(0, 0, width, height);
        startRasterSessionFromCanvas(canvas, {
          source: 'blank',
          mode: 'coloring_book',
          fixedBounds: true,
        });
      }

      async function startEditOnBoardFromFile() {
        const file = (dom.fileInput.files && dom.fileInput.files[0]) || state.lastSketchFile;
        if (!file || !String(file.type || '').startsWith('image/')) {
          pushFeed('Choose a raster image first.', 'error');
          return;
        }
        const mode = readImagePreprocessMode();
        let canvas;
        if (mode === 'coloring_book') {
          canvas = await loadImageToCanvas(file);
        } else {
          const form = new FormData();
          form.append('file', file);
          form.append('settings_json', JSON.stringify(readFilePreviewSettings()));
          const payload = await apiRequest('/api/preprocess/lineart', {
            method: 'POST',
            body: form,
            timeoutMs: 300000,
          });
          const lineartUrl = payload.lineart_data_url
            || (payload.preprocess_preview && payload.preprocess_preview.lineart_data_url);
          if (!lineartUrl) {
            throw new Error('Preprocess did not return lineart.');
          }
          canvas = await loadImageToCanvas(lineartUrl);
        }
        startRasterSessionFromCanvas(canvas, { source: 'file', mode });
      }

      function drawBoardRasterSession(layout) {
        const session = state.boardRasterSession;
        if (!session?.active || !session.memoryCanvas || !session.imageBoundsBoard) {
          return;
        }
        const canvasRect = boardRectToCanvasRect(layout, session.imageBoundsBoard);
        if (canvasRect.width <= 0 || canvasRect.height <= 0) {
          return;
        }
        const crop = session.cropNormalized || defaultCropNormalized();
        const pixelRect = BoardFab.imageCropToPixelRect
          ? BoardFab.imageCropToPixelRect(
            crop,
            session.memoryCanvas.width,
            session.memoryCanvas.height,
          )
          : null;
        ctx.save();
        ctx.imageSmoothingEnabled = true;
        if (typeof ctx.imageSmoothingQuality !== 'undefined') {
          ctx.imageSmoothingQuality = 'high';
        }
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(canvasRect.x, canvasRect.y, canvasRect.width, canvasRect.height);
        if (pixelRect) {
          ctx.drawImage(
            session.memoryCanvas,
            pixelRect.sx,
            pixelRect.sy,
            pixelRect.sw,
            pixelRect.sh,
            canvasRect.x,
            canvasRect.y,
            canvasRect.width,
            canvasRect.height,
          );
        } else {
          ctx.drawImage(
            session.memoryCanvas,
            canvasRect.x,
            canvasRect.y,
            canvasRect.width,
            canvasRect.height,
          );
        }
        ctx.restore();
      }

      function drawRasterCropOverlay(layout) {
        const session = state.boardRasterSession;
        if (!session?.active || state.boardOverlayMode !== 'crop' || !session.imageBoundsBoard) {
          return;
        }
        const imageBounds = session.imageBoundsBoard;
        const cropRect = session.cropRectBoard || imageBounds;
        const imageCanvasRect = boardRectToCanvasRect(layout, imageBounds);
        const cropCanvasRect = boardRectToCanvasRect(layout, cropRect);

        ctx.save();
        roundedRect(ctx, layout.originX, layout.originY, layout.boardPixelWidth, layout.boardPixelHeight, 22);
        ctx.clip();
        ctx.fillStyle = 'rgba(15, 23, 42, 0.38)';
        ctx.fillRect(layout.originX, layout.originY, layout.boardPixelWidth, layout.boardPixelHeight);
        ctx.globalCompositeOperation = 'destination-out';
        ctx.fillRect(
          imageCanvasRect.x,
          imageCanvasRect.y,
          imageCanvasRect.width,
          imageCanvasRect.height,
        );
        ctx.globalCompositeOperation = 'source-over';
        ctx.strokeStyle = 'rgba(37, 99, 235, 0.95)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 4]);
        ctx.strokeRect(
          cropCanvasRect.x,
          cropCanvasRect.y,
          cropCanvasRect.width,
          cropCanvasRect.height,
        );

        const cx = (cropRect.xMin + cropRect.xMax) * 0.5;
        const cy = (cropRect.yMin + cropRect.yMax) * 0.5;
        const handles = [
          { x: cropRect.xMin, y: cropRect.yMax },
          { x: cx, y: cropRect.yMax },
          { x: cropRect.xMax, y: cropRect.yMax },
          { x: cropRect.xMax, y: cy },
          { x: cropRect.xMax, y: cropRect.yMin },
          { x: cx, y: cropRect.yMin },
          { x: cropRect.xMin, y: cropRect.yMin },
          { x: cropRect.xMin, y: cy },
        ];
        ctx.setLineDash([]);
        ctx.fillStyle = '#ffffff';
        handles.forEach((handle) => {
          const point = boardToCanvas(layout, handle.x, handle.y);
          ctx.beginPath();
          ctx.arc(point.x, point.y, 5, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        });
        ctx.restore();
      }

      function beginRasterMoveInteraction(event) {
        const session = state.boardRasterSession;
        if (!session?.active || session.phase !== 'edit' || session.fixedBounds) {
          return false;
        }
        if (state.boardEditMode || state.boardOverlayMode === 'crop') {
          return false;
        }
        const layout = computeLayout();
        const rect = dom.canvas.getBoundingClientRect();
        const boardPoint = canvasToBoard(layout, event.clientX - rect.left, event.clientY - rect.top);
        const hit = BoardFab.cropHandleHit
          ? BoardFab.cropHandleHit(session.imageBoundsBoard, boardPoint)
          : null;
        if (hit !== 'move') {
          return false;
        }
        session.moveInteraction = {
          pointerId: event.pointerId,
          startBoard: boardPoint,
          startBounds: { ...session.imageBoundsBoard },
        };
        dom.canvas.setPointerCapture(event.pointerId);
        dom.canvas.style.cursor = 'grabbing';
        return true;
      }

      function moveRasterMoveInteraction(event) {
        const session = state.boardRasterSession;
        const interaction = session?.moveInteraction;
        if (!interaction) {
          return false;
        }
        const layout = computeLayout();
        const rect = dom.canvas.getBoundingClientRect();
        const boardPoint = canvasToBoard(layout, event.clientX - rect.left, event.clientY - rect.top);
        const dx = boardPoint.x - interaction.startBoard.x;
        const dy = boardPoint.y - interaction.startBoard.y;
        const moved = BoardFab.moveRect
          ? BoardFab.moveRect(interaction.startBounds, dx, dy, boardSafeClampBounds())
          : null;
        if (moved) {
          session.imageBoundsBoard = moved;
          session.cropRectBoard = { ...moved };
          session.dirty = true;
        }
        return true;
      }

      function endRasterMoveInteraction(event) {
        const session = state.boardRasterSession;
        if (!session?.moveInteraction) {
          return false;
        }
        try {
          dom.canvas.releasePointerCapture(event.pointerId);
        } catch (_error) {
          // ignore
        }
        session.moveInteraction = null;
        clampSessionImageBounds(session);
        session.cropRectBoard = session.imageBoundsBoard
          ? { ...session.imageBoundsBoard }
          : null;
        dom.canvas.style.cursor = 'default';
        syncBoardEditCanvasOverlay(computeLayout());
        syncBoardFabState();
        return true;
      }

      function beginRasterCropInteraction(event) {
        const session = state.boardRasterSession;
        if (!session?.active || session.phase !== 'edit' || session.fixedBounds || state.boardOverlayMode !== 'crop') {
          return false;
        }
        const layout = computeLayout();
        const rect = dom.canvas.getBoundingClientRect();
        const boardPoint = canvasToBoard(layout, event.clientX - rect.left, event.clientY - rect.top);
        const cropRect = session.cropRectBoard || session.imageBoundsBoard;
        const handle = BoardFab.cropHandleHit ? BoardFab.cropHandleHit(cropRect, boardPoint) : null;
        if (!handle) {
          return false;
        }
        session.cropInteraction = {
          pointerId: event.pointerId,
          handle,
          startRect: BoardFab.normalizeBoardRect ? BoardFab.normalizeBoardRect(cropRect) : cropRect,
          startBoard: boardPoint,
        };
        dom.canvas.setPointerCapture(event.pointerId);
        dom.canvas.style.cursor = handle === 'move' ? 'grabbing' : 'crosshair';
        return true;
      }

      function moveRasterCropInteraction(event) {
        const session = state.boardRasterSession;
        const interaction = session?.cropInteraction;
        if (!interaction) {
          return false;
        }
        const layout = computeLayout();
        const rect = dom.canvas.getBoundingClientRect();
        const boardPoint = canvasToBoard(layout, event.clientX - rect.left, event.clientY - rect.top);
        const fullBounds = rasterFullBoundsBoard(session);
        const imageClamp = fullBounds
          ? boardFabBoundsFromRect(fullBounds)
          : boardFabBoundsFromRect(session.imageBoundsBoard);
        let nextRect = null;
        if (interaction.handle === 'move') {
          const dx = boardPoint.x - interaction.startBoard.x;
          const dy = boardPoint.y - interaction.startBoard.y;
          nextRect = BoardFab.moveRect
            ? BoardFab.moveRect(interaction.startRect, dx, dy, imageClamp)
            : null;
        } else {
          nextRect = BoardFab.resizeRect
            ? BoardFab.resizeRect(interaction.startRect, interaction.handle, boardPoint)
            : null;
          if (nextRect && imageClamp) {
            const clamped = BoardFab.clampRectToBounds
              ? BoardFab.clampRectToBounds(nextRect, imageClamp)
              : nextRect;
            if (clamped) {
              const width = clamped.xMax - clamped.xMin;
              const height = clamped.yMax - clamped.yMin;
              if (
                width >= (BoardFab.MIN_CROP_SIZE_M || 0.01)
                && height >= (BoardFab.MIN_CROP_SIZE_M || 0.01)
              ) {
                nextRect = clamped;
              } else {
                nextRect = null;
              }
            } else {
              nextRect = null;
            }
          }
        }
        if (nextRect) {
          session.cropRectBoard = nextRect;
          session.dirty = true;
        }
        return true;
      }

      function endRasterCropInteraction(event) {
        const session = state.boardRasterSession;
        if (!session?.cropInteraction) {
          return false;
        }
        try {
          dom.canvas.releasePointerCapture(event.pointerId);
        } catch (_error) {
          // ignore
        }
        const cropRect = session.cropRectBoard;
        if (cropRect) {
          applyRasterCropFromBoardRect(session);
        }
        session.cropInteraction = null;
        dom.canvas.style.cursor = 'default';
        syncBoardEditCanvasOverlay(computeLayout());
        refreshUiState({ redrawBoard: true });
        return true;
      }

      function syncBoardEditCanvasOverlay(layout) {
        if (!dom.boardEditCanvas) {
          return;
        }
        const session = state.boardRasterSession;
        if (!session?.active || session.phase !== 'edit' || !session.imageBoundsBoard || !state.boardEditMode) {
          dom.boardEditCanvas.classList.remove('active');
          dom.boardEditCanvas.style.display = 'none';
          return;
        }
        const canvasRect = boardRectToCanvasRect(layout, session.imageBoundsBoard);
        const width = Math.max(1, canvasRect.width);
        const height = Math.max(1, canvasRect.height);
        const dpr = Math.max(1, window.devicePixelRatio || 1);
        dom.boardEditCanvas.classList.add('active');
        dom.boardEditCanvas.style.display = 'block';
        dom.boardEditCanvas.style.position = 'absolute';
        dom.boardEditCanvas.style.inset = 'auto';
        dom.boardEditCanvas.style.left = `${canvasRect.x}px`;
        dom.boardEditCanvas.style.top = `${canvasRect.y}px`;
        dom.boardEditCanvas.style.width = `${width}px`;
        dom.boardEditCanvas.style.height = `${height}px`;
        const pixelWidth = Math.round(width * dpr);
        const pixelHeight = Math.round(height * dpr);
        if (dom.boardEditCanvas.width !== pixelWidth || dom.boardEditCanvas.height !== pixelHeight) {
          dom.boardEditCanvas.width = pixelWidth;
          dom.boardEditCanvas.height = pixelHeight;
          if (editCtx) {
            editCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
          }
        }
        if (editCtx) {
          editCtx.clearRect(0, 0, width, height);
        }
      }

      function boardEditToolRadiusScreenPx(session, layout) {
        const canvasRect = boardRectToCanvasRect(layout, session.imageBoundsBoard);
        const overlayWidth = Math.max(1, canvasRect.width);
        const lineWidthMem = rasterPenLineWidthMemoryPx(session);
        const crop = session.cropNormalized || defaultCropNormalized();
        const cropWidthPx = Math.max(
          1,
          session.memoryCanvas.width * (crop.xMax - crop.xMin),
        );
        const lineWidthScreen = (lineWidthMem / cropWidthPx) * overlayWidth;
        return Math.max(0.75, lineWidthScreen * 0.5);
      }

      function drawBoardEditToolCursor(layout) {
        const session = state.boardRasterSession;
        if (
          !session?.active
          || session.phase !== 'edit'
          || !state.boardEditMode
          || !state.boardEditToolPointer
          || !session.imageBoundsBoard
        ) {
          return;
        }
        const { x, y } = state.boardEditToolPointer;
        const radius = boardEditToolRadiusScreenPx(session, layout);
        ctx.save();
        roundedRect(ctx, layout.originX, layout.originY, layout.boardPixelWidth, layout.boardPixelHeight, 22);
        ctx.clip();
        if (state.boardEditMode === 'eraser') {
          ctx.strokeStyle = 'rgba(37, 99, 235, 0.95)';
          ctx.fillStyle = 'rgba(37, 99, 235, 0.08)';
        } else {
          ctx.strokeStyle = 'rgba(17, 24, 39, 0.9)';
          ctx.fillStyle = 'rgba(17, 24, 39, 0.06)';
        }
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();
      }

      function updateBoardEditToolPointer(event) {
        if (!dom.boardEditCanvas || !state.boardEditMode) {
          return;
        }
        const rect = dom.canvas.getBoundingClientRect();
        state.boardEditToolPointer = {
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
        };
      }

      function finalizeRasterBoardStroke() {
        const session = state.boardRasterSession;
        if (!session?.memoryCanvas || !state.boardEditLastPoint) {
          state.boardEditLastPoint = null;
          state.boardEditSmoothPoint = null;
          return;
        }
        const point = state.boardEditLastPoint;
        const start = state.boardEditSmoothPoint;
        if (start && (start.x !== point.x || start.y !== point.y)) {
          const memCtx = session.memoryCanvas.getContext('2d');
          const lineWidth = rasterPenLineWidthMemoryPx(session);
          memCtx.imageSmoothingEnabled = true;
          memCtx.lineCap = 'round';
          memCtx.lineJoin = 'round';
          memCtx.lineWidth = lineWidth;
          if (state.boardEditMode === 'eraser') {
            memCtx.strokeStyle = '#ffffff';
          } else {
            memCtx.strokeStyle = '#000000';
          }
          memCtx.beginPath();
          memCtx.moveTo(start.x, start.y);
          memCtx.lineTo(point.x, point.y);
          memCtx.stroke();
          session.dirty = true;
        }
        state.boardEditLastPoint = null;
        state.boardEditSmoothPoint = null;
      }

      function rasterPenLineWidthMemoryPx(session) {
        if (!session?.memoryCanvas || !session.imageBoundsBoard) {
          return 1;
        }
        const crop = session.cropNormalized || defaultCropNormalized();
        const cropWidthPx = Math.max(
          1,
          session.memoryCanvas.width * (crop.xMax - crop.xMin),
        );
        const widthM = Math.max(1.0e-6, session.imageBoundsBoard.xMax - session.imageBoundsBoard.xMin);
        const radiusM = activeBoardBrushRadiusM(session);
        const memScale = cropWidthPx / widthM;
        return Math.max(1, 2 * radiusM * memScale);
      }

      function paintRasterBoardEdit(event) {
        const session = state.boardRasterSession;
        if (!session?.active || session.phase !== 'edit' || !state.boardEditMode || !session.memoryCanvas) {
          return;
        }
        const point = boardEditPointerToMemoryPixel(event);
        if (!point) {
          return;
        }
        const memCtx = session.memoryCanvas.getContext('2d');
        const lineWidth = rasterPenLineWidthMemoryPx(session);
        memCtx.imageSmoothingEnabled = true;
        memCtx.lineCap = 'round';
        memCtx.lineJoin = 'round';
        memCtx.lineWidth = lineWidth;
        if (state.boardEditMode === 'eraser') {
          memCtx.globalCompositeOperation = 'source-over';
          memCtx.strokeStyle = '#ffffff';
          memCtx.fillStyle = '#ffffff';
        } else {
          memCtx.globalCompositeOperation = 'source-over';
          memCtx.strokeStyle = '#000000';
          memCtx.fillStyle = '#000000';
        }
        if (state.boardEditLastPoint) {
          const start = state.boardEditSmoothPoint || state.boardEditLastPoint;
          const midX = (state.boardEditLastPoint.x + point.x) * 0.5;
          const midY = (state.boardEditLastPoint.y + point.y) * 0.5;
          memCtx.beginPath();
          memCtx.moveTo(start.x, start.y);
          memCtx.quadraticCurveTo(state.boardEditLastPoint.x, state.boardEditLastPoint.y, midX, midY);
          memCtx.stroke();
          state.boardEditSmoothPoint = { x: midX, y: midY };
        } else {
          memCtx.beginPath();
          memCtx.arc(point.x, point.y, lineWidth * 0.5, 0, Math.PI * 2);
          memCtx.fill();
          state.boardEditSmoothPoint = { x: point.x, y: point.y };
        }
        memCtx.globalCompositeOperation = 'source-over';
        state.boardEditLastPoint = point;
        session.dirty = true;
        syncBoardFabState();
        refreshUiState({ redrawBoard: true });
      }

      function rasterSessionVectorizeSettings() {
        const session = state.boardRasterSession;
        const settings = readFilePreviewSettings();
        if (session?.vectorizationMethod) {
          settings.vectorization_method = session.vectorizationMethod;
        }
        if (!session?.memoryCanvas || !session.imageBoundsBoard || !BoardFab.placementFromImageBounds) {
          return settings;
        }
        const fitBounds = boardSafeClampBounds();
        const useInkPlacement = Boolean(session.fixedBounds || session.source === 'blank');
        const marginM = useInkPlacement
          ? 0
          : (DEBUG_MODE
            ? Number(dom.sketchMargin?.value || BOARD_RASTER_FIT_MARGIN_M)
            : BOARD_RASTER_FIT_MARGIN_M);
        let placement = null;
        if (useInkPlacement && BoardFab.placementFromInkBounds) {
          placement = BoardFab.placementFromInkBounds(
            session.imageBoundsBoard,
            session.memoryCanvas,
            fitBounds,
            marginM,
            session.cropNormalized || defaultCropNormalized(),
          );
        }
        if (!placement) {
          placement = BoardFab.placementFromImageBounds(
            session.imageBoundsBoard,
            session.memoryCanvas.width,
            session.memoryCanvas.height,
            fitBounds,
            marginM,
            session.cropNormalized || defaultCropNormalized(),
          );
        }
        if (useInkPlacement) {
          settings.max_image_dim = 0;
        }
        if (placement) {
          settings.center_x_m = placement.center_x_m;
          settings.center_y_m = placement.center_y_m;
          settings.scale_percent = placement.scale_percent;
          settings.fit_to_safe_area = true;
          settings.margin_m = marginM;
        }
        return settings;
      }

      async function vectorizeBoardRasterSession(session) {
        const settings = rasterSessionVectorizeSettings();
        const aiEnabled = settings.image_preprocess_mode === 'photo'
          || (settings.image_preprocess_mode === 'coloring_book' && !settings.image_raw_print);

        // Board edit already ran AI preprocess when opening photo mode (startEditOnBoardFromFile).
        // Always vectorize from the session canvas so Potrace/AutoTrace toggles do not re-run SwinIR.
        const exportCanvas = exportRasterSessionCanvas(session);
        const blob = await new Promise((resolve) => exportCanvas.toBlob(resolve, 'image/png'));
        if (!blob) {
          throw new Error('Failed to export edited lineart.');
        }
        const previewFile = new File([blob], 'edited-lineart.png', { type: 'image/png' });

        const form = new FormData();
        form.append('file', previewFile);
        form.append('settings_json', JSON.stringify(settings));

        if (aiEnabled) {
          form.append('input_type', 'auto');
        }

        return apiRequest('/api/preview/edited-lineart', {
          method: 'POST',
          body: form,
          timeoutMs: aiEnabled ? 300000 : 180000,
        });
      }

      async function revectorizeBoardRasterSession(method) {
        const session = state.boardRasterSession;
        if (!session?.active || session.phase !== 'preview_ready' || state.boardFabConfirmBusy) {
          return;
        }
        const normalized = method === 'potrace' ? 'potrace' : 'autotrace';
        session.vectorizationMethod = normalized;
        syncBoardFabVectorMethodUi();
        state.boardFabConfirmBusy = true;
        syncBoardFabState();
        try {
          const payload = await vectorizeBoardRasterSession(session);
          applySketchCenterlinePreview(payload, { revectorizeOnly: true });
          session.phase = 'preview_ready';
          refreshUiState({ redrawBoard: true });
          pushFeed(
            `Preview updated (${normalized === 'potrace' ? 'Potrace' : 'AutoTrace'}). Tap ✓ again to draw.`,
            'success',
          );
        } finally {
          state.boardFabConfirmBusy = false;
          syncBoardFabState();
        }
      }

      async function confirmBoardFabAction() {
        const session = state.boardRasterSession;
        if (!session?.active || state.boardFabConfirmBusy) {
          return;
        }
        if (session.phase === 'edit') {
          clampSessionImageBounds(session);
          const useInkValidation = Boolean(session.fixedBounds || session.source === 'blank');
          if (useInkValidation) {
            if (!inkBoundsInsideSketchValidation(session)) {
              pushFeed(
                'Draw on the blank page inside the dashed frame before Confirm.',
                'error',
              );
              return;
            }
          } else if (!imageBoundsInsideSketchValidation(session.imageBoundsBoard)) {
            pushFeed(
              'Sketch placement is outside the robot-safe drawable bounds; move or crop inside the dashed frame.',
              'error',
            );
            return;
          }
          state.boardFabConfirmBusy = true;
          syncBoardFabState();
          try {
            const payload = await vectorizeBoardRasterSession(session);
            applySketchCenterlinePreview(payload);
            session.phase = 'preview_ready';
            if (dom.boardFabConfirm) {
              dom.boardFabConfirm.classList.add('ready');
            }
            setBoardEditMode(null);
            setBoardOverlayMode(null);
            syncBoardFabState();
            refreshUiState({ redrawBoard: true });
            pushFeed('Preview ready on board — tap ✓ again to draw with the robot.', 'success');
            setNotice(
              'success',
              'Preview ready',
              'The blank page is hidden. Board Workspace shows the vector preview. Tap ✓ again to draw.',
            );
          } finally {
            state.boardFabConfirmBusy = false;
            syncBoardFabState();
          }
          return;
        }
        if (session.phase === 'preview_ready') {
          state.boardFabConfirmBusy = true;
          syncBoardFabState();
          try {
            await drawUploadedFile();
            clearBoardRasterSession();
            refreshUiState({ redrawBoard: true });
          } finally {
            state.boardFabConfirmBusy = false;
            syncBoardFabState();
          }
        }
      }

      function syncBoardFabState() {
        const sessionActive = isRasterEditActive();
        const inEdit = sessionActive && state.boardRasterSession?.phase === 'edit';
        if (dom.boardFabConfirm) {
          dom.boardFabConfirm.disabled = !sessionActive || state.boardFabConfirmBusy;
          const ready = state.boardRasterSession?.phase === 'preview_ready';
          dom.boardFabConfirm.classList.toggle('ready', ready);
          dom.boardFabConfirm.title = state.boardFabConfirmBusy
            ? 'Working…'
            : (ready ? 'Draw — publish to robot' : 'Confirm — generate preview');
        }
        if (dom.boardFabCrop) {
          const cropAllowed = inEdit && !state.boardRasterSession?.fixedBounds;
          dom.boardFabCrop.disabled = !cropAllowed;
          dom.boardFabCrop.classList.toggle('active', state.boardOverlayMode === 'crop');
        }
        if (dom.boardFabPen) {
          dom.boardFabPen.disabled = !inEdit;
          dom.boardFabPen.classList.toggle('active', state.boardEditMode === 'pen');
        }
        if (dom.boardFabEraser) {
          dom.boardFabEraser.disabled = !inEdit;
          dom.boardFabEraser.classList.toggle('active', state.boardEditMode === 'eraser');
        }
        if (dom.boardFabBlank) {
          const blankActive = inEdit && state.boardRasterSession?.source === 'blank';
          dom.boardFabBlank.disabled = sessionActive && !blankActive;
          dom.boardFabBlank.classList.toggle('active', blankActive);
          dom.boardFabBlank.title = blankActive
            ? 'Hide blank page'
            : 'Blank canvas — draw on an empty page';
        }
        syncBoardFabBrushSizeUi();
        syncBoardFabVectorMethodUi();
        if (dom.boardFabClearPreview) {
          const canClearPreview = Boolean(
            state.currentPreviewId
            || state.sketchPreviewUrl
            || previewIsBoardVisible()
            || isRasterEditActive(),
          );
          dom.boardFabClearPreview.disabled = !canClearPreview;
        }
      }

      function syncBoardFabVectorMethodUi() {
        const session = state.boardRasterSession;
        const show = Boolean(session?.active);
        if (dom.boardFabVectorMethod) {
          dom.boardFabVectorMethod.hidden = !show;
        }
        if (!show) {
          return;
        }
        const method = session.vectorizationMethod === 'potrace' ? 'potrace' : 'autotrace';
        session.vectorizationMethod = method;
        dom.boardFabVectorButtons.forEach((button) => {
          const buttonMethod = button.dataset.boardVectorMethod === 'potrace' ? 'potrace' : 'autotrace';
          const active = buttonMethod === method;
          button.classList.toggle('active', active);
          const available = buttonMethod === 'potrace'
            ? state.potraceAvailable !== false
            : state.autotraceAvailable !== false;
          button.disabled = state.boardFabConfirmBusy || !available;
        });
      }

      async function setBoardRasterVectorizationMethod(method) {
        const session = state.boardRasterSession;
        if (!session?.active || state.boardFabConfirmBusy) {
          return;
        }
        const normalized = method === 'potrace' ? 'potrace' : 'autotrace';
        if (normalized === session.vectorizationMethod && session.phase === 'preview_ready') {
          return;
        }
        session.vectorizationMethod = normalized;
        syncBoardFabVectorMethodUi();
        if (session.phase === 'preview_ready') {
          await revectorizeBoardRasterSession(normalized);
        }
      }

      function activeBrushDiameterMaxMm() {
        return state.boardEditMode === 'eraser' ? ERASER_DIAMETER_MAX_MM : BRUSH_DIAMETER_MAX_MM;
      }

      function brushDiameterMmFromRadius(radiusM, maxMm = BRUSH_DIAMETER_MAX_MM) {
        return Math.max(
          BRUSH_DIAMETER_MIN_MM,
          Math.min(maxMm, Number(radiusM || 0.003) * 2000),
        );
      }

      function brushRadiusMFromDiameterMm(diameterMm, maxMm = BRUSH_DIAMETER_MAX_MM) {
        return Math.max(
          BRUSH_DIAMETER_MIN_MM / 2000,
          Math.min(maxMm / 2000, Number(diameterMm) / 2000),
        );
      }

      function syncBoardFabBrushSizeUi() {
        const showBrush = isRasterEditing() && (
          state.boardEditMode === 'eraser' || state.boardEditMode === 'pen'
        );
        if (dom.boardFabBrushSize) {
          dom.boardFabBrushSize.hidden = !showBrush;
        }
        if (dom.boardFabStack) {
          dom.boardFabStack.classList.toggle('brush-controls-visible', showBrush);
        }
        if (dom.boardFabBrushLabel) {
          dom.boardFabBrushLabel.textContent = state.boardEditMode === 'eraser' ? 'Eraser' : 'Pen';
        }
        const brushAnchor = state.boardEditMode === 'eraser'
          ? dom.boardFabBrushAnchorEraser
          : dom.boardFabBrushAnchorPen;
        if (brushAnchor && dom.boardFabBrushSize && dom.boardFabBrushSize.parentElement !== brushAnchor) {
          brushAnchor.appendChild(dom.boardFabBrushSize);
        }
        const maxDiameterMm = activeBrushDiameterMaxMm();
        if (dom.boardFabBrushRange) {
          dom.boardFabBrushRange.max = String(maxDiameterMm);
        }
        const diameterMm = brushDiameterMmFromRadius(activeBoardBrushRadiusM(), maxDiameterMm);
        if (dom.boardFabBrushRange) {
          dom.boardFabBrushRange.value = String(Math.round(diameterMm));
        }
        if (dom.boardFabBrushReadout) {
          dom.boardFabBrushReadout.textContent = `${diameterMm.toFixed(1)} mm`;
        }
      }

      function applyBoardFabBrushDiameterMm(diameterMm) {
        const maxDiameterMm = activeBrushDiameterMaxMm();
        const radiusM = brushRadiusMFromDiameterMm(diameterMm, maxDiameterMm);
        const session = state.boardRasterSession;
        if (state.boardEditMode === 'eraser') {
          state.eraserTipRadiusM = radiusM;
          if (session?.active) {
            session.eraserTipRadiusM = radiusM;
          }
        } else {
          state.penTipRadiusM = radiusM;
          if (session?.active) {
            session.penTipRadiusM = radiusM;
          }
        }
        syncBoardFabBrushSizeUi();
        refreshUiState({ redrawBoard: true });
      }

      function setBoardOverlayMode(mode) {
        const session = state.boardRasterSession;
        if (mode === 'crop' && session?.fixedBounds) {
          return;
        }
        state.boardOverlayMode = mode || null;
        if (session) {
          session.cropInteraction = null;
          session.moveInteraction = null;
          if (mode === 'crop' && session.imageBoundsBoard) {
            session.cropRectBoard = { ...session.imageBoundsBoard };
          }
        }
        if (mode) {
          setBoardEditMode(null);
        }
        syncBoardFabState();
        refreshUiState({ redrawBoard: true });
      }

      function setBoardFabExpanded(expanded) {
        state.boardFabExpanded = Boolean(expanded);
        if (dom.boardFabStack) {
          dom.boardFabStack.classList.toggle('expanded', state.boardFabExpanded);
        }
        if (dom.boardFabMain) {
          dom.boardFabMain.classList.toggle('active', state.boardFabExpanded);
          dom.boardFabMain.setAttribute('aria-expanded', state.boardFabExpanded ? 'true' : 'false');
        }
      }

      function toggleBoardFullscreen() {
        if (!document.fullscreenElement) {
          if (dom.boardStage && dom.boardStage.requestFullscreen) {
            dom.boardStage.requestFullscreen().catch(() => {});
          }
        } else {
          document.exitFullscreen();
        }
      }

      document.addEventListener('fullscreenchange', () => {
        state.boardFullscreen = !!document.fullscreenElement;
        if (dom.boardStage) {
          dom.boardStage.classList.toggle('board-fullscreen', state.boardFullscreen);
        }
        refreshUiState({ redrawBoard: true });
      });

      function defaultBoardEditMemorySize() {
        const board = currentBoard();
        const bounds = board?.bounds;
        if (bounds) {
          const widthM = Math.max(0.01, Number(bounds.x_max) - Number(bounds.x_min));
          const heightM = Math.max(0.01, Number(bounds.y_max) - Number(bounds.y_min));
          const aspect = widthM / heightM;
          const base = 1024;
          if (aspect >= 1) {
            return { width: base, height: Math.max(256, Math.round(base / aspect)) };
          }
          return { width: Math.max(256, Math.round(base * aspect)), height: base };
        }
        return { width: 1024, height: 1024 };
      }

      function setBoardEditMode(mode) {
        state.boardEditMode = mode;
        state.boardEditLastPoint = null;
        state.boardEditSmoothPoint = null;
        if (mode !== 'pen' && mode !== 'eraser') {
          state.boardEditToolPointer = null;
        }
        if (dom.boardEditCanvas) {
          if (mode === 'eraser' || mode === 'pen') {
            dom.boardEditCanvas.style.cursor = 'none';
          } else {
            dom.boardEditCanvas.style.cursor = 'default';
          }
        }
        if (isRasterEditActive()) {
          syncBoardEditCanvasOverlay(computeLayout());
        } else if (dom.boardEditCanvas) {
          dom.boardEditCanvas.classList.remove('active');
          dom.boardEditCanvas.style.display = 'none';
        }
        syncBoardFabState();
      }

      function boardEditPointerToMemoryPixel(event) {
        const session = state.boardRasterSession;
        if (!session?.memoryCanvas) {
          return null;
        }
        const rect = dom.boardEditCanvas.getBoundingClientRect();
        const localX = event.clientX - rect.left;
        const localY = event.clientY - rect.top;
        const overlayWidth = rect.width;
        const overlayHeight = rect.height;
        if (overlayWidth <= 0 || overlayHeight <= 0) {
          return null;
        }
        const crop = session.cropNormalized || defaultCropNormalized();
        const pixelRect = BoardFab.imageCropToPixelRect
          ? BoardFab.imageCropToPixelRect(
            crop,
            session.memoryCanvas.width,
            session.memoryCanvas.height,
          )
          : null;
        if (!pixelRect) {
          return null;
        }
        return {
          x: pixelRect.sx + ((localX / overlayWidth) * pixelRect.sw),
          y: pixelRect.sy + ((localY / overlayHeight) * pixelRect.sh),
        };
      }

      function drawPreviewPlacementControls(layout) {
        return;
      }

      function previewHandleHit(layout, canvasX, canvasY) {
        if (
          !previewIsBoardVisible()
          || isRasterEditing()
          || state.boardEditMode
          || state.boardOverlayMode === 'crop'
          || state.previewDirty
          || !state.currentPreviewId
        ) {
          return null;
        }
        const bounds = transformedPreviewBounds();
        if (!bounds) {
          return null;
        }
        const boardPoint = canvasToBoard(layout, canvasX, canvasY);
        const inside = (
          boardPoint.x >= bounds.xMin
          && boardPoint.x <= bounds.xMax
          && boardPoint.y >= bounds.yMin
          && boardPoint.y <= bounds.yMax
        );
        if (!inside) {
          return null;
        }
        return {
          mode: 'drag',
          bounds,
        };
      }

      function previewRequestOrigin(previewState = state.vectorPreview) {
        return previewState && previewState.origin ? String(previewState.origin) : null;
      }

      function schedulePreviewRefresh() {
        markPreviewSettingsChanged();
      }

      function bumpSketchPreviewGeneration() {
        state.sketchPreviewGeneration += 1;
        state.sketchPreviewBusy = false;
        return state.sketchPreviewGeneration;
      }

      function revokeSketchPreviewArtifacts() {
        clearSketchBoardOverlay();
        if (state.vectorPreview) {
          revokePreviewResources(state.vectorPreview);
          state.vectorPreview = null;
        }
        state.previewInteraction = null;
        state.currentPreviewId = null;
        state.sketchPreviewId = null;
        state.currentCanonicalHash = null;
        state.currentPrimitiveHash = null;
        state.currentExecutionHash = null;
        state.currentSettingsHash = null;
        state.currentExecutionPreviewSvg = '';
        state.currentMetrics = null;
        state.currentInput = null;
        state.currentProcessingSettings = null;
        state.currentPipelineMode = null;
        state.previewDirty = false;
        if (state.sketchPreviewUrl) {
          URL.revokeObjectURL(state.sketchPreviewUrl);
          state.sketchPreviewUrl = null;
        }
        state.sketchPreviewSvgText = '';
        state.sketchPreviewPayload = null;
        if (dom.sketchPreviewBox) {
          dom.sketchPreviewBox.classList.remove('active');
        }
        if (dom.sketchPreviewImg) {
          dom.sketchPreviewImg.removeAttribute('src');
          dom.sketchPreviewImg.style.display = 'none';
        }
        if (dom.sketchPreviewDiag) {
          dom.sketchPreviewDiag.textContent = 'No executable preview loaded yet.';
        }
        if (dom.previewMetricsGrid) {
          dom.previewMetricsGrid.innerHTML = '';
        }
        if (dom.pipelineModeLabel) {
          dom.pipelineModeLabel.textContent = 'Adaptive Centerline';
        }
      }

      function clearSketchBoardPreviewForNewFile() {
        bumpSketchPreviewGeneration();
        clearBoardRasterSession();
        revokeSketchPreviewArtifacts();
        dom.previewChip.innerHTML = '<strong>Preview</strong> none';
        dom.summaryPreview.textContent = 'No preview loaded yet.';
        resetSketchDrawState('Draw is unavailable until Generate Preview runs for this file.');
      }

      function clearSketchPreviewForBoardEdit() {
        bumpSketchPreviewGeneration();
        revokeSketchPreviewArtifacts();
        dom.previewChip.innerHTML = '<strong>Board</strong> editing on workspace';
        dom.summaryPreview.textContent = 'Move, crop, or draw on the raster layer, then confirm to vectorize.';
        resetSketchDrawState('Confirm on the board to generate a preview from your edits.');
      }

      function hideBoardPreviewOverlay() {
        clearSketchBoardOverlay();
        if (state.vectorPreview) {
          state.vectorPreview.boardVisible = false;
        }
      }

      function clearVectorPreview(pushMessage = false) {
        revokePreviewResources(state.vectorPreview);
        state.vectorPreview = null;
        state.previewInteraction = null;
        state.currentPreviewId = null;
        state.currentCanonicalHash = null;
        state.currentPrimitiveHash = null;
        state.currentExecutionHash = null;
        state.currentSettingsHash = null;
        state.currentExecutionPreviewSvg = '';
        state.currentMetrics = null;
        state.currentInput = null;
        state.currentProcessingSettings = null;
        state.currentPipelineMode = null;
        state.previewDirty = false;
        resetSketchDrawState('Draw is available after Generate Preview returns a valid preview_id.');
        clearBoardRasterSession();
        if (state.previewRefreshTimer) {
          clearTimeout(state.previewRefreshTimer);
          state.previewRefreshTimer = null;
        }
        if (dom.sketchPreviewBox) {
          dom.sketchPreviewBox.classList.remove('active');
        }
        if (dom.sketchPreviewImg) {
          dom.sketchPreviewImg.removeAttribute('src');
        }
        if (dom.sketchPreviewDiag) {
          dom.sketchPreviewDiag.textContent = 'No executable preview loaded yet.';
        }
        if (dom.previewMetricsGrid) {
          dom.previewMetricsGrid.innerHTML = '';
        }
        if (dom.pipelineModeLabel) {
          dom.pipelineModeLabel.textContent = 'Adaptive Centerline';
        }
        dom.previewChip.innerHTML = '<strong>Preview</strong> none';
        dom.summaryPreview.textContent = 'No preview loaded yet.';
        if (state.activeTool === 'text') {
          syncPlacementDefaults(true);
        }
        refreshUiState({ redrawBoard: true });
        if (pushMessage) {
          pushFeed('Preview cleared.', 'info');
        }
      }

      function markPreviewSettingsChanged() {
        if (!state.currentPreviewId || state.previewDirty) {
          return;
        }
        state.previewDirty = true;
        setSketchDrawStatus('Settings changed. Generate Preview again before drawing.');
        setNotice('warning', 'Preview out of date', 'Settings changed. Generate Preview again before drawing.');
        refreshUiState({ redrawBoard: true });
      }

      function hideBoardPreviewAfterDraw(statusMessage) {
        if (state.vectorPreview) {
          state.vectorPreview.boardVisible = false;
        }
        dom.previewChip.innerHTML = '<strong>Preview</strong> hidden while drawing';
        dom.summaryPreview.textContent = statusMessage || 'Draw published. Board Workspace is now showing live robot/trail output only.';
        setSketchDrawStatus(statusMessage || 'Draw published. Board preview hidden so you can evaluate the robot trail.');
        refreshUiState({ redrawBoard: true });
      }

      async function clearCurrentPreview() {
        const previewId = state.currentPreviewId;
        if (previewId) {
          await apiRequest(`/api/preview/${encodeURIComponent(previewId)}`, {
            method: 'DELETE',
          });
        }
        clearVectorPreview(true);
      }

      function buildRasterOverlay(imageUrl, overlayPayload) {
        if (!imageUrl || !overlayPayload || !overlayPayload.bounds) {
          return null;
        }
        const imageElement = new Image();
        const rasterOverlay = {
          imageUrl,
          imageElement,
          loaded: false,
          bounds: overlayPayload.bounds,
          imageSize: overlayPayload.image_size || null,
        };
        imageElement.addEventListener('load', () => {
          rasterOverlay.loaded = true;
          refreshUiState({ redrawBoard: true });
        });
        imageElement.src = imageUrl;
        return rasterOverlay;
      }

      function applyRasterPreview(sourceType, payload, imageUrl, { origin = null } = {}) {
        const drawRequest = payload.draw_request || null;
        const placement = drawRequest && drawRequest.placement
          ? clonePlacement(drawRequest.placement)
          : (() => {
            try {
              return readPlacement();
            } catch (_error) {
              return defaultPlacementForTool(state.activeTool);
            }
          })();
        const nextState = {
          sourceType,
          preview: null,
          drawRequest,
          origin: origin || sourceType,
          boardVisible: true,
          basePlacement: placement,
          displayPlacement: placement,
          rasterOverlay: buildRasterOverlay(imageUrl, payload.raster_overlay),
        };
        revokePreviewResources(state.vectorPreview);
        resetSketchDrawState('Draw is unavailable until Generate Preview succeeds.');
        state.vectorPreview = nextState;
        if (drawRequest && drawRequest.placement) {
          setPlacementInputs(drawRequest.placement);
        }
        const imageInfo = payload.image_info || {};
        const processing = payload.status || {};
        dom.previewChip.innerHTML = `<strong>Preview</strong> ${escapeHtml(sourceType)} · processing`;
        dom.summaryPreview.textContent = `${sourceType} upload stored. ${processing.message || 'Processing preview in background.'}`;
        clearNotice();
        pushFeed(`Upload stored for ${sourceType}; waiting for vector preview.`, 'info');
        refreshUiState({ redrawBoard: true });
      }

      function applyVectorPreview(sourceType, payload, { origin = null, boardVisible = null } = {}) {
        const drawRequest = payload.draw_request || null;
        const placement = drawRequest && drawRequest.placement
          ? clonePlacement(drawRequest.placement)
          : (() => {
            try {
              return readPlacement();
            } catch (_error) {
              return defaultPlacementForTool(state.activeTool);
            }
          })();
        revokePreviewResources(state.vectorPreview);
        resetSketchDrawState('Draw is available after Generate Preview returns a valid preview_id.');
        if (dom.sketchPreviewBox) {
          dom.sketchPreviewBox.classList.remove('active');
        }
        if (dom.sketchPreviewImg) {
          dom.sketchPreviewImg.removeAttribute('src');
        }
        if (dom.sketchPreviewDiag) {
          dom.sketchPreviewDiag.textContent = 'No sketch preview loaded yet.';
        }
        state.vectorPreview = {
          sourceType,
          preview: payload.preview,
          drawRequest,
          origin: origin || sourceType,
          boardVisible: boardVisible === null ? true : Boolean(boardVisible),
          basePlacement: placement,
          displayPlacement: placement,
          rasterOverlay: null,
        };
        state.currentPreviewId = payload.preview_id || null;
        state.currentCanonicalHash = payload.canonical_hash || null;
        state.currentPrimitiveHash = payload.primitive_hash || null;
        state.currentExecutionHash = payload.execution_hash || null;
        state.currentSettingsHash = payload.settings_hash || null;
        state.currentExecutionPreviewSvg = String(payload.execution_preview_svg || '');
        state.currentMetrics = payload.metrics || null;
        state.currentInput = sourceType;
        state.currentProcessingSettings = drawRequest || {};
        state.currentPipelineMode = payload.pipeline_mode || sourceType;
        state.previewDirty = false;
        if (dom.pipelineModeLabel) {
          dom.pipelineModeLabel.textContent = pipelineDisplayName(payload);
        }
        renderSketchPreviewSvg(payload);

        const preview = payload.preview || {};
        const metrics = payload.metrics || {};
        const strokeCount = Number(metrics.draw_path_count || preview.stroke_count || 0);
        const pointCount = Number(metrics.draw_sample_count || preview.point_count || 0);
        dom.previewChip.innerHTML = `<strong>Preview</strong> ${escapeHtml(sourceType)} · ${strokeCount} paths`;
        dom.summaryPreview.textContent = `${sourceType} executable preview ready · ${strokeCount} drawable paths · ${pointCount} samples · pipeline ${state.currentPipelineMode || sourceType}.`;

        if (drawRequest && drawRequest.placement) {
          setPlacementInputs(drawRequest.placement);
        }
        if (
          sourceType === 'text'
          && drawRequest
          && Object.prototype.hasOwnProperty.call(drawRequest, 'font_source')
        ) {
          setTextFontSource(drawRequest.font_source);
        }

        if (preview.validation_error) {
          setNotice('warning', 'Preview requires adjustment', preview.validation_error);
          pushFeed(`Preview warning: ${preview.validation_error}`, 'error');
        } else {
          clearNotice();
          pushFeed(`Executable preview ready for ${sourceType}.`, 'success');
        }
        setSketchDrawStatus(state.currentPreviewId ? 'Draw is ready and will publish the cached primitive payload.' : 'Draw is disabled because no preview_id was returned.');
        refreshUiState({ redrawBoard: true });
        refreshDebugPanels().catch(() => {});
      }

      function updateSummary() {
        const display = displayBounds();
        dom.summaryBoard.textContent = `Reachable writing area ${(
          display.xMax - display.xMin
        ).toFixed(2)}m × ${(
          display.yMax - display.yMin
        ).toFixed(2)}m · x ${formatRange(display.xMin, display.xMax)} / y ${formatRange(display.yMin, display.yMax)}`;
      }

      function updateMetrics() {
        const board = currentBoard();
        const display = displayBounds();
        dom.metricBoard.textContent = `${(display.xMax - display.xMin).toFixed(3)} × ${(display.yMax - display.yMin).toFixed(3)} m`;
        dom.metricWritable.textContent = `${formatRange(display.xMin, display.xMax)} · ${formatRange(display.yMin, display.yMax)}`;
        if (state.robotPose) {
          dom.metricRobot.textContent = `${state.robotPose.x.toFixed(3)}, ${state.robotPose.y.toFixed(3)} · θ ${state.robotPose.theta.toFixed(3)}`;
        } else {
          dom.metricRobot.textContent = '--';
        }
        if (state.penPose) {
          const contactState = state.penContact ? 'press' : 'release';
          dom.metricPen.textContent = `${formatPoint(state.penPose.x, state.penPose.y)} · ${contactState}`;
        } else {
          dom.metricPen.textContent = '--';
        }
        dom.trailChip.innerHTML = `<strong>Trail</strong> ${state.trailPointCount} points`;
        if (dom.stripBoard) {
          dom.stripBoard.textContent = `${(display.xMax - display.xMin).toFixed(2)} × ${(display.yMax - display.yMin).toFixed(2)} m`;
        }
        if (dom.stripTrail) {
          dom.stripTrail.textContent = `${state.trailPointCount} pts`;
        }
        if (dom.stripPreview && dom.summaryPreview) {
          const previewText = dom.summaryPreview.textContent || 'none';
          dom.stripPreview.textContent = previewText.length > 72 ? `${previewText.slice(0, 69)}…` : previewText;
        }
        dom.statusExecutor.textContent = state.runtime && state.runtime.statuses ? (state.runtime.statuses.cable_executor_status || '--') : '--';
        dom.statusSupervisor.textContent = state.runtime && state.runtime.statuses ? (state.runtime.statuses.cable_supervisor_status || '--') : '--';
        dom.manualPenStatus.textContent = state.manualPenMode || '--';
      }

      function updateDiagnostics() {
        const runtime = state.runtime;
        const preview = state.vectorPreview && state.vectorPreview.preview ? state.vectorPreview.preview : null;
        const previewDiagnostics = preview && preview.diagnostics ? preview.diagnostics : null;
        if (!runtime) {
          dom.runtimeActiveMode.textContent = '--';
          dom.runtimeReady.textContent = '--';
          dom.runtimeBoardFrame.textContent = '--';
          dom.runtimeSafeWorkspace.textContent = '--';
          dom.runtimeNotReady.textContent = '--';
          dom.runtimeWebotsTrail.textContent = '--';
        } else {
          dom.runtimeActiveMode.textContent = runtime.active_mode || '--';
          dom.runtimeReady.textContent = runtime.ready ? 'ready' : 'not_ready';
          dom.runtimeNotReady.textContent = runtime.not_ready_reason || 'none';
          dom.runtimeWebotsTrail.textContent = runtime.enable_webots_trail ? 'enabled' : 'disabled';

          if (runtime.board_info) {
            const info = runtime.board_info;
            dom.runtimeBoardFrame.textContent = `${info.frame_origin || '--'} · x:${info.frame_x_axis || '--'} · y:${info.frame_y_axis || '--'}`;
            const safeText = `${formatRange(info.safe_x_min, info.safe_x_max)} · ${formatRange(info.safe_y_min, info.safe_y_max)}`;
            if (Number.isFinite(Number(info.body_safe_x_min))) {
              dom.runtimeSafeWorkspace.textContent = `${safeText} · body ${formatRange(info.body_safe_x_min, info.body_safe_x_max)}`;
            } else {
              dom.runtimeSafeWorkspace.textContent = safeText;
            }
          } else {
            dom.runtimeBoardFrame.textContent = '--';
            dom.runtimeSafeWorkspace.textContent = '--';
          }
        }

        if (!previewDiagnostics) {
          dom.previewSamplingDiag.textContent = '--';
          dom.runtimeSamplingDiag.textContent = '--';
          dom.previewParityDiag.textContent = '--';
          dom.canonicalPlanDiag.textContent = '--';
          return;
        }

        const previewSampling = previewDiagnostics.preview_sampling || {};
        const runtimeSampling = previewDiagnostics.runtime_sampling || {};
        const parity = previewDiagnostics.parity || {};
        const canonicalPlan = previewDiagnostics.canonical_plan || {};
        const previewPolicy = previewSampling.policy || {};
        const runtimePolicy = runtimeSampling.policy || {};
        const pointBudget = previewDiagnostics.point_budget || {};

        dom.previewSamplingDiag.textContent = `draw ${previewSampling.draw_point_count || 0} pts / travel ${previewSampling.travel_point_count || 0} pts · step ${Number(previewPolicy.draw_step_m || previewPolicy.curve_tolerance_m || 0).toFixed(3)}m`;
        dom.runtimeSamplingDiag.textContent = `draw ${runtimeSampling.draw_point_count || 0} pts / travel ${runtimeSampling.travel_point_count || 0} pts · step ${Number(runtimePolicy.draw_step_m || runtimePolicy.curve_tolerance_m || 0).toFixed(3)}m`;
        dom.previewParityDiag.textContent = `${parity.status || '--'} · Δpts ${pointBudget.delta_points || 0} · Δbounds ${Number(parity.bounds_delta_max_m || 0).toFixed(4)}m`;
        const primitiveCounts = canonicalPlan.primitive_counts || {};
        dom.canonicalPlanDiag.textContent = `${canonicalPlan.command_count || 0} cmds · L ${primitiveCounts.LineSegment || 0} · A ${primitiveCounts.ArcSegment || 0} · Q ${primitiveCounts.QuadraticBezier || 0} · C ${primitiveCounts.CubicBezier || 0}`;
      }

      function refreshUiState({ redrawBoard = false } = {}) {
        if (redrawBoard) {
          renderBoard();
        } else if (isRasterEditActive() && state.boardEditMode) {
          syncBoardEditCanvasOverlay(computeLayout());
        }
        updateSummary();
        updateMetrics();
        updateDiagnostics();
        updateRuntimeTone();
        syncControls();
        refreshOverlay();
        syncBoardFabState();
      }

      function updateRuntimeTone() {
        if (!state.runtime) {
          setStatusPill(dom.runtimePill, dom.runtimeText, 'connecting', 'Runtime · waiting');
          return;
        }
        const ready = Boolean(state.runtime.ready);
        const activeMode = state.runtime.active_mode || 'off';
        setStatusPill(dom.runtimePill, dom.runtimeText, ready ? 'ready' : 'connecting', ready ? `Runtime · ${activeMode}` : 'Runtime · waiting');
      }

      function syncControls() {
        const runtimeReady = Boolean(state.runtime && state.runtime.ready);
        const backendHealthy = state.backend !== 'error';
        const drawReady = Boolean(runtimeReady && state.currentPreviewId && !state.previewDirty);
        const executorBusy = Boolean(state.runtime && state.runtime.statuses && state.runtime.statuses.cable_executor_status === 'running');
        dom.manualPenButtons.forEach((button) => {
          const active = state.manualPenMode === button.dataset.manualPen;
          button.classList.toggle('active', active);
          button.disabled = !runtimeReady || executorBusy || active;
        });

        if (dom.textSubmitBtn) {
          dom.textSubmitBtn.disabled = !runtimeReady || !backendHealthy || state.activeTool !== 'text';
        }
        if (dom.textClearBtn) {
          dom.textClearBtn.disabled = !canUndoLastWrite(readTextColumn());
        }
        if (dom.svgPreviewBtn) dom.svgPreviewBtn.disabled = !backendHealthy;
        if (dom.svgSubmitBtn) dom.svgSubmitBtn.disabled = !drawReady || state.activeTool !== 'svg';
        if (dom.fileUploadBtn) {
          dom.fileUploadBtn.disabled = !backendHealthy || state.sketchPreviewBusy || isRasterEditActive();
        }
        if (dom.fileEditBoardBtn) {
          const hasFile = Boolean((dom.fileInput.files && dom.fileInput.files[0]) || state.lastSketchFile);
          dom.fileEditBoardBtn.disabled = !hasFile || state.sketchPreviewBusy || isRasterEditActive();
        }
        if (dom.sketchDownloadSvgBtn) dom.sketchDownloadSvgBtn.disabled = !state.sketchPreviewUrl;
        if (dom.sketchDownloadMetricsBtn) dom.sketchDownloadMetricsBtn.disabled = !state.currentMetrics;
        if (dom.fileDrawBtn) {
          dom.fileDrawBtn.disabled = !drawReady || state.activeTool !== 'file' || state.sketchDrawBusy;
        }
        const inTextTab = state.activeTool === 'text';
        if (dom.voiceCaptureBtn) {
          dom.voiceCaptureBtn.disabled = !runtimeReady || !backendHealthy || !inTextTab;
        }
        if (dom.emergencyStopBtn) {
          dom.emergencyStopBtn.disabled = !runtimeReady || !backendHealthy;
        }
      }

      function setOverlay(title, copy, visible) {
        dom.overlayTitle.textContent = title;
        dom.overlayCopy.textContent = copy;
        dom.overlayCard.classList.toggle('visible', visible);
      }

      function refreshOverlay() {
        if (state.backend === 'error') {
          setOverlay('Backend unavailable', 'The FastAPI backend is currently unreachable. The board will stay in local preview mode only.', true);
          return;
        }

        if (state.rosbridge !== 'connected') {
          setOverlay('Connecting to rosbridge', 'Live robot and pen telemetry will appear once rosbridge connects.', true);
          return;
        }

        if (!state.board) {
          setOverlay('Waiting for board metadata', 'The browser is connected, but /wall_climber/board_info has not arrived yet.', true);
          return;
        }

        if (!state.robotPose && !state.penPose) {
          setOverlay('Waiting for live poses', 'Board geometry is ready. Waiting for robot_pose_board and pen_pose_board.', true);
          return;
        }

        setOverlay('', '', false);
      }

      function parseBoardInfo(raw) {
        try {
          const parsed = JSON.parse(raw);
          if (!Number.isFinite(Number(parsed.width)) || !Number.isFinite(Number(parsed.height))) {
            return null;
          }
          return {
            width: Number(parsed.width),
            height: Number(parsed.height),
            frame_origin: parsed.frame_origin || 'top_left',
            frame_x_axis: parsed.frame_x_axis || 'right',
            frame_y_axis: parsed.frame_y_axis || 'down',
            writable_x_min: Number(parsed.writable_x_min),
            writable_x_max: Number(parsed.writable_x_max),
            writable_y_min: Number(parsed.writable_y_min),
            writable_y_max: Number(parsed.writable_y_max),
            safe_x_min: Number(parsed.safe_x_min),
            safe_x_max: Number(parsed.safe_x_max),
            safe_y_min: Number(parsed.safe_y_min),
            safe_y_max: Number(parsed.safe_y_max),
            body_safe_x_min: safeNumber(parsed.body_safe_x_min),
            body_safe_x_max: safeNumber(parsed.body_safe_x_max),
            body_safe_y_min: safeNumber(parsed.body_safe_y_min),
            body_safe_y_max: safeNumber(parsed.body_safe_y_max),
            line_height: safeNumber(parsed.line_height),
            corner_keepout_radius: safeNumber(parsed.corner_keepout_radius),
            anchors: parsed.anchors || null,
          };
        } catch (_error) {
          return null;
        }
      }

      function distance(a, b) {
        return Math.hypot(a.x - b.x, a.y - b.y);
      }

      function trimTrail() {
        while (state.trailPointCount > MAX_TRAIL_POINTS && state.trailSegments.length > 0) {
          const first = state.trailSegments[0];
          if (!first || first.length === 0) {
            state.trailSegments.shift();
            continue;
          }
          first.shift();
          state.trailPointCount -= 1;
          if (first.length === 0) {
            state.trailSegments.shift();
          }
        }
      }

      function clearTrail() {
        state.trailSegments = [];
        state.activeTrailSegment = null;
        state.lastTrailPoint = null;
        state.trailPointCount = 0;
        clearAllColumnDrafts();
        TEXT_COLUMNS.forEach((column) => {
          state.textWriteUndoStack[column] = [];
        });
        state.pendingWriteUndo = null;
        resetBackendTextCursor().catch(() => {});
        pushFeed('Trail and text session cleared.', 'info');
      }

      function beginTrailSegment(point) {
        const segment = [point];
        state.trailSegments.push(segment);
        state.activeTrailSegment = segment;
        state.lastTrailPoint = point;
        state.trailPointCount += 1;
        trimTrail();
      }

      function endTrailSegment() {
        state.activeTrailSegment = null;
        state.lastTrailPoint = null;
      }

      function appendTrailPoint(point) {
        if (!state.penContact) {
          return;
        }
        if (!state.activeTrailSegment) {
          beginTrailSegment(point);
          return;
        }
        if (state.lastTrailPoint && distance(state.lastTrailPoint, point) < MIN_TRAIL_POINT_DIST) {
          return;
        }
        state.activeTrailSegment.push(point);
        state.lastTrailPoint = point;
        state.trailPointCount += 1;
        trimTrail();
      }

      function computeLayout() {
        const board = currentBoard();
        const view = displayBounds();
        const viewWidth = Math.max(1.0e-6, view.xMax - view.xMin);
        const viewHeight = Math.max(1.0e-6, view.yMax - view.yMin);
        const stageWidth = Math.max(320, dom.boardStage.clientWidth);
        const fullscreen = Boolean(state.boardFullscreen);
        const maxHeight = fullscreen ? window.innerHeight : Math.max(560, window.innerHeight - 140);
        let desiredHeight;
        if (fullscreen) {
          desiredHeight = maxHeight;
        } else {
          desiredHeight = Math.min(maxHeight, Math.max(520, stageWidth / (viewWidth / viewHeight)));
        }
        dom.boardStage.style.height = `${Math.round(desiredHeight)}px`;

        const padding = fullscreen ? 12 : 16;
        const drawableWidth = stageWidth - (padding * 2);
        const drawableHeight = desiredHeight - (padding * 2);
        const scaleX = drawableWidth / viewWidth;
        const scaleY = drawableHeight / viewHeight;
        const scale = Math.min(scaleX, scaleY);
        const boardPixelWidth = viewWidth * scale;
        const boardPixelHeight = viewHeight * scale;
        const originX = (stageWidth - boardPixelWidth) * 0.5;
        const originY = (desiredHeight - boardPixelHeight) * 0.5;

        return {
          cssWidth: stageWidth,
          cssHeight: desiredHeight,
          board,
          view,
          scale,
          originX,
          originY,
          boardPixelWidth,
          boardPixelHeight,
        };
      }

      function resizeCanvas(layout) {
        const dpr = Math.max(1, window.devicePixelRatio || 1);
        const width = Math.round(layout.cssWidth * dpr);
        const height = Math.round(layout.cssHeight * dpr);
        if (dom.canvas.width !== width || dom.canvas.height !== height) {
          dom.canvas.width = width;
          dom.canvas.height = height;
        }
        dom.canvas.style.width = `${Math.round(layout.cssWidth)}px`;
        dom.canvas.style.height = `${Math.round(layout.cssHeight)}px`;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }

      function boardToCanvas(layout, x, y) {
        return {
          x: layout.originX + ((x - layout.view.xMin) * layout.scale),
          y: layout.originY + ((y - layout.view.yMin) * layout.scale),
        };
      }

      function canvasToBoard(layout, x, y) {
        return {
          x: layout.view.xMin + ((x - layout.originX) / layout.scale),
          y: layout.view.yMin + ((y - layout.originY) / layout.scale),
        };
      }

      function boardRectToCanvasRect(layout, rect) {
        const a = boardToCanvas(layout, rect.xMin, rect.yMin);
        const b = boardToCanvas(layout, rect.xMax, rect.yMax);
        return {
          x: Math.min(a.x, b.x),
          y: Math.min(a.y, b.y),
          width: Math.abs(b.x - a.x),
          height: Math.abs(b.y - a.y),
        };
      }

      function roundedRect(context, x, y, width, height, radius) {
        const r = Math.min(radius, width * 0.5, height * 0.5);
        context.beginPath();
        context.moveTo(x + r, y);
        context.arcTo(x + width, y, x + width, y + height, r);
        context.arcTo(x + width, y + height, x, y + height, r);
        context.arcTo(x, y + height, x, y, r);
        context.arcTo(x, y, x + width, y, r);
        context.closePath();
      }

      function drawBackground(layout) {
        const gradient = ctx.createLinearGradient(0, 0, 0, layout.cssHeight);
        gradient.addColorStop(0, '#eef3f6');
        gradient.addColorStop(1, '#f7f9fb');
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, layout.cssWidth, layout.cssHeight);
      }

      function drawBoardBase(layout) {
        const x = layout.originX;
        const y = layout.originY;
        const w = layout.boardPixelWidth;
        const h = layout.boardPixelHeight;

        roundedRect(ctx, x - 8, y - 8, w + 16, h + 16, 26);
        ctx.fillStyle = '#dfe6ec';
        ctx.fill();

        roundedRect(ctx, x, y, w, h, 22);
        ctx.fillStyle = '#ffffff';
        ctx.fill();

        ctx.save();
        roundedRect(ctx, x, y, w, h, 22);
        ctx.clip();

        const subtle = ctx.createLinearGradient(x, y, x + w, y + h);
        subtle.addColorStop(0, 'rgba(255,255,255,0.98)');
        subtle.addColorStop(1, 'rgba(246,248,250,0.92)');
        ctx.fillStyle = subtle;
        ctx.fillRect(x, y, w, h);

        ctx.strokeStyle = 'rgba(54, 86, 111, 0.05)';
        ctx.lineWidth = 1;
        const step = Math.max(12, layout.scale * 0.2);
        for (let ix = x; ix <= x + w; ix += step) {
          ctx.beginPath();
          ctx.moveTo(ix, y);
          ctx.lineTo(ix, y + h);
          ctx.stroke();
        }
        for (let iy = y; iy <= y + h; iy += step) {
          ctx.beginPath();
          ctx.moveTo(x, iy);
          ctx.lineTo(x + w, iy);
          ctx.stroke();
        }
        ctx.restore();
      }

      function drawZones(layout) {
        const safe = isRasterEditActive() ? sketchValidationBounds() : textSafeBounds();
        const safeTopLeft = boardToCanvas(layout, safe.xMin, safe.yMin);
        const safeBottomRight = boardToCanvas(layout, safe.xMax, safe.yMax);

        roundedRect(
          ctx,
          safeTopLeft.x,
          safeTopLeft.y,
          safeBottomRight.x - safeTopLeft.x,
          safeBottomRight.y - safeTopLeft.y,
          14
        );
        ctx.fillStyle = 'rgba(54, 86, 111, 0.08)';
        ctx.fill();
        ctx.strokeStyle = 'rgba(54, 86, 111, 0.28)';
        ctx.setLineDash([7, 8]);
        ctx.lineWidth = 1.4;
        ctx.stroke();
        ctx.setLineDash([]);
      }

      function drawRobot(layout) {
        if (!state.robotPose) {
          return;
        }
        const center = boardToCanvas(layout, state.robotPose.x, state.robotPose.y);
        const width = CARRIAGE.width * layout.scale;
        const height = CARRIAGE.height * layout.scale;
        const x = center.x - (width * 0.5);
        const y = center.y - (height * 0.5);

        roundedRect(ctx, x, y, width, height, 10);
        ctx.fillStyle = '#ef7b4d';
        ctx.fill();
        ctx.strokeStyle = 'rgba(35, 24, 21, 0.55)';
        ctx.lineWidth = 1.4;
        ctx.stroke();

        roundedRect(ctx, x + (width * 0.12), y + (height * 0.16), width * 0.70, height * 0.70, 8);
        ctx.fillStyle = '#222a31';
        ctx.fill();
        roundedRect(ctx, x + (width * 0.62), y + (height * 0.20), width * 0.18, height * 0.58, 6);
        ctx.fillStyle = '#4c5762';
        ctx.fill();

        const shoulder = boardToCanvas(
          layout,
          state.robotPose.x + CARRIAGE.armBaseX,
          state.robotPose.y + CARRIAGE.armOffsetY
        );
        const mid = boardToCanvas(
          layout,
          state.robotPose.x + CARRIAGE.armMidX,
          state.robotPose.y + CARRIAGE.armOffsetY
        );
        const wrist = boardToCanvas(
          layout,
          state.robotPose.x + CARRIAGE.wristX,
          state.robotPose.y + CARRIAGE.penOffsetY
        );
        const holder = boardToCanvas(
          layout,
          state.robotPose.x + CARRIAGE.holderX,
          state.robotPose.y + CARRIAGE.penOffsetY
        );
        const penBodyStart = boardToCanvas(
          layout,
          state.robotPose.x + (CARRIAGE.penOffsetX - 0.003),
          state.robotPose.y + (CARRIAGE.penOffsetY - 0.009)
        );
        const penBodyEnd = boardToCanvas(
          layout,
          state.robotPose.x + (CARRIAGE.penOffsetX - 0.003),
          state.robotPose.y + (CARRIAGE.penOffsetY + 0.021)
        );
        const penFrontBand = boardToCanvas(
          layout,
          state.robotPose.x + (CARRIAGE.penOffsetX - 0.003),
          state.robotPose.y + (CARRIAGE.penOffsetY - 0.0015)
        );
        const penBackCap = boardToCanvas(
          layout,
          state.robotPose.x + (CARRIAGE.penOffsetX - 0.003),
          state.robotPose.y + (CARRIAGE.penOffsetY + 0.016)
        );

        ctx.save();
        ctx.lineCap = 'round';
        ctx.fillStyle = '#272e35';
        roundedRect(ctx, shoulder.x - 9, shoulder.y - 11, 16, 22, 6);
        ctx.fill();
        ctx.fillStyle = '#55616d';
        roundedRect(ctx, shoulder.x + 1, shoulder.y - 7, 14, 14, 5);
        ctx.fill();

        const bracketLeadX = shoulder.x + 4;
        const bracketLeadW = Math.max(16, mid.x - shoulder.x + 1);
        ctx.fillStyle = '#647583';
        roundedRect(ctx, bracketLeadX, shoulder.y - 8, bracketLeadW, 6, 3);
        ctx.fill();
        roundedRect(ctx, bracketLeadX, shoulder.y + 2, bracketLeadW, 6, 3);
        ctx.fill();
        ctx.fillStyle = '#50606c';
        roundedRect(ctx, mid.x - 3, mid.y - 8, Math.max(14, wrist.x - mid.x + 5), 16, 5);
        ctx.fill();

        ctx.fillStyle = '#161b20';
        roundedRect(ctx, wrist.x - 5, wrist.y - 8, 12, 16, 5);
        ctx.fill();
        ctx.fillStyle = '#2d353d';
        roundedRect(ctx, wrist.x + 1, wrist.y - 4, 12, 8, 4);
        ctx.fill();

        ctx.fillStyle = '#2a323a';
        roundedRect(ctx, holder.x - 12, holder.y - 4, 12, 8, 4);
        ctx.fill();

        const cradleRadius = Math.max(5.4, layout.scale * 0.016);
        const cradleThickness = Math.max(2.1, layout.scale * 0.007);
        ctx.strokeStyle = '#191d22';
        ctx.lineWidth = cradleThickness;
        ctx.beginPath();
        ctx.arc(holder.x, holder.y, cradleRadius, Math.PI * 0.34, Math.PI * 1.66, false);
        ctx.stroke();

        ctx.strokeStyle = '#bf2020';
        ctx.lineWidth = Math.max(2.4, layout.scale * 0.012);
        ctx.beginPath();
        ctx.moveTo(penBodyStart.x, penBodyStart.y);
        ctx.lineTo(penBodyEnd.x, penBodyEnd.y);
        ctx.stroke();

        ctx.strokeStyle = '#171b20';
        ctx.lineWidth = Math.max(1.5, layout.scale * 0.008);
        ctx.beginPath();
        ctx.arc(penFrontBand.x, penFrontBand.y, Math.max(4.4, layout.scale * 0.016), 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(penBackCap.x, penBackCap.y, Math.max(4.8, layout.scale * 0.017), 0, Math.PI * 2);
        ctx.stroke();

        ctx.fillStyle = state.penContact ? '#0f1318' : '#58626d';
        ctx.beginPath();
        ctx.arc(penBodyStart.x, penBodyStart.y, Math.max(1.8, layout.scale * 0.0075), 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();

        const leftAttach = boardToCanvas(layout, state.robotPose.x - CARRIAGE.attachX, state.robotPose.y - CARRIAGE.attachY);
        const rightAttach = boardToCanvas(layout, state.robotPose.x + CARRIAGE.attachX, state.robotPose.y - CARRIAGE.attachY);
        [leftAttach, rightAttach].forEach((point) => {
          ctx.beginPath();
          ctx.arc(point.x, point.y, 4.2, 0, Math.PI * 2);
          ctx.fillStyle = '#2f3740';
          ctx.fill();
        });
      }

      function drawTrail(layout) {
        if (state.trailSegments.length === 0) {
          return;
        }
        ctx.save();
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.strokeStyle = 'rgba(32, 38, 44, 0.9)';
        ctx.lineWidth = Math.max(1.4, Math.min(3.0, layout.scale * 0.011));
        state.trailSegments.forEach((segment) => {
          if (!segment || segment.length < 2) {
            return;
          }
          const first = boardToCanvas(layout, segment[0].x, segment[0].y);
          ctx.beginPath();
          ctx.moveTo(first.x, first.y);
          for (let index = 1; index < segment.length; index += 1) {
            const point = boardToCanvas(layout, segment[index].x, segment[index].y);
            ctx.lineTo(point.x, point.y);
          }
          ctx.stroke();
        });
        ctx.restore();
      }

      function pointArrayToCanvas(layout, point) {
        if (!Array.isArray(point) || point.length < 2) {
          return null;
        }
        const x = safeNumber(point[0]);
        const y = safeNumber(point[1]);
        if (x === null || y === null) {
          return null;
        }
        return boardToCanvas(layout, x, y);
      }

      function debugPointToArray(point) {
        if (Array.isArray(point) && point.length >= 2) {
          return [Number(point[0]), Number(point[1])];
        }
        if (point && typeof point === 'object') {
          return [Number(point.x), Number(point.y)];
        }
        return null;
      }

      function drawStrokePath(layout, stroke, { strokeStyle, lineWidth, alpha = 1, dashed = false } = {}) {
        if (!Array.isArray(stroke) || stroke.length < 2) {
          return;
        }
        const first = pointArrayToCanvas(layout, stroke[0]);
        if (!first) {
          return;
        }
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = strokeStyle || previewStrokeColor();
        ctx.lineWidth = lineWidth || Math.max(1.3, Math.min(3.2, layout.scale * 0.012));
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        if (dashed) {
          ctx.setLineDash([8, 7]);
        }
        ctx.beginPath();
        ctx.moveTo(first.x, first.y);
        for (let index = 1; index < stroke.length; index += 1) {
          const point = pointArrayToCanvas(layout, stroke[index]);
          if (!point) {
            continue;
          }
          ctx.lineTo(point.x, point.y);
        }
        ctx.stroke();
        ctx.restore();
      }

      function sampleDebugPrimitive(primitive) {
        if (!primitive || typeof primitive !== 'object') {
          return [];
        }
        const type = primitive.type;
        if (type === 'line') {
          const start = debugPointToArray(primitive.start);
          const end = debugPointToArray(primitive.end);
          return start && end ? [start, end] : [];
        }
        if (type === 'arc') {
          const center = debugPointToArray(primitive.center);
          const radius = Number(primitive.radius);
          const startAngle = Number(primitive.start_angle_rad);
          const sweep = Number(primitive.sweep_angle_rad);
          if (!center || !Number.isFinite(radius) || !Number.isFinite(startAngle) || !Number.isFinite(sweep)) {
            return [];
          }
          const segments = Math.max(12, Math.min(72, Math.ceil(Math.abs(sweep) / (Math.PI / 18))));
          const points = [];
          for (let index = 0; index <= segments; index += 1) {
            const t = segments === 0 ? 0 : index / segments;
            const angle = startAngle + (sweep * t);
            points.push([
              center[0] + (radius * Math.cos(angle)),
              center[1] + (radius * Math.sin(angle)),
            ]);
          }
          return points;
        }
        if (type === 'quadratic') {
          const start = debugPointToArray(primitive.start);
          const control = debugPointToArray(primitive.control);
          const end = debugPointToArray(primitive.end);
          if (!start || !control || !end) {
            return [];
          }
          const points = [];
          for (let index = 0; index <= 28; index += 1) {
            const t = index / 28;
            const omt = 1 - t;
            points.push([
              (omt * omt * start[0]) + (2 * omt * t * control[0]) + (t * t * end[0]),
              (omt * omt * start[1]) + (2 * omt * t * control[1]) + (t * t * end[1]),
            ]);
          }
          return points;
        }
        if (type === 'cubic') {
          const start = debugPointToArray(primitive.start);
          const control1 = debugPointToArray(primitive.control1);
          const control2 = debugPointToArray(primitive.control2);
          const end = debugPointToArray(primitive.end);
          if (!start || !control1 || !control2 || !end) {
            return [];
          }
          const points = [];
          for (let index = 0; index <= 40; index += 1) {
            const t = index / 40;
            const omt = 1 - t;
            points.push([
              (omt ** 3 * start[0]) + (3 * omt * omt * t * control1[0]) + (3 * omt * t * t * control2[0]) + (t ** 3 * end[0]),
              (omt ** 3 * start[1]) + (3 * omt * omt * t * control1[1]) + (3 * omt * t * t * control2[1]) + (t ** 3 * end[1]),
            ]);
          }
          return points;
        }
        return [];
      }

      function primitiveColor(type, colorByType) {
        if (!colorByType) {
          return 'rgba(54, 86, 111, 0.86)';
        }
        if (type === 'arc') {
          return 'rgba(45, 143, 132, 0.95)';
        }
        if (type === 'quadratic') {
          return 'rgba(94, 125, 54, 0.95)';
        }
        if (type === 'cubic') {
          return 'rgba(29, 78, 216, 0.95)';
        }
        return 'rgba(37, 99, 235, 0.95)';
      }

      function drawSketchSvgBoardOverlay(layout) {
        if (isRasterEditing()) {
          return false;
        }
        if (!sketchBoardOverlayActive()) {
          return false;
        }
        const boardWidth = Number(layout.board.width);
        const boardHeight = Number(layout.board.height);
        if (!Number.isFinite(boardWidth) || !Number.isFinite(boardHeight) || boardWidth <= 0 || boardHeight <= 0) {
          return false;
        }
        const topLeft = boardToCanvas(layout, 0, 0);
        const bottomRight = boardToCanvas(layout, boardWidth, boardHeight);
        const x = Math.min(topLeft.x, bottomRight.x);
        const y = Math.min(topLeft.y, bottomRight.y);
        const width = Math.abs(bottomRight.x - topLeft.x);
        const height = Math.abs(bottomRight.y - topLeft.y);
        if (width <= 0 || height <= 0) {
          return false;
        }

        ctx.save();
        roundedRect(ctx, layout.originX, layout.originY, layout.boardPixelWidth, layout.boardPixelHeight, 22);
        ctx.clip();
        ctx.globalAlpha = 0.92;
        ctx.drawImage(state.sketchBoardOverlayImage, x, y, width, height);
        ctx.restore();
        return true;
      }

      function drawVectorPreview(layout) {
        const previewState = state.vectorPreview;
        if (!previewIsBoardVisible(previewState)) {
          return;
        }
        if (state.currentExecutionPreviewSvg && drawSketchSvgBoardOverlay(layout)) {
          return;
        }
        const preview = previewState && previewState.preview ? previewState.preview : null;
        if (!preview || !Array.isArray(preview.strokes) || preview.strokes.length === 0) {
          const rasterOverlay = previewState && previewState.rasterOverlay ? previewState.rasterOverlay : null;
          const bounds = transformedPreviewBounds(previewState);
          if (!rasterOverlay || !bounds) {
            return;
          }
          const topLeft = boardToCanvas(layout, bounds.xMin, bounds.yMin);
          const bottomRight = boardToCanvas(layout, bounds.xMax, bounds.yMax);
          const width = Math.max(4, bottomRight.x - topLeft.x);
          const height = Math.max(4, bottomRight.y - topLeft.y);
          ctx.save();
          ctx.globalAlpha = 0.88;
          if (rasterOverlay.loaded) {
            ctx.drawImage(rasterOverlay.imageElement, topLeft.x, topLeft.y, width, height);
          } else {
            ctx.fillStyle = 'rgba(17, 24, 39, 0.08)';
            ctx.fillRect(topLeft.x, topLeft.y, width, height);
          }
          ctx.strokeStyle = 'rgba(17, 24, 39, 0.55)';
          ctx.lineWidth = 1.6;
          ctx.strokeRect(topLeft.x, topLeft.y, width, height);
          ctx.restore();
          return;
        }
        const invalid = preview.can_draw === false && !['sketch_image', 'sketch_centerline'].includes(previewState.sourceType);
        const lineWidth = previewBoardLineWidth(layout);
        const strokeAlpha = isRasterSessionPreviewReady() ? 0.98 : (invalid ? 0.55 : 0.9);
        transformedPreviewStrokes(previewState).forEach((stroke) => {
          drawStrokePath(layout, stroke, {
            strokeStyle: previewStrokeColor(),
            lineWidth,
            alpha: strokeAlpha,
            dashed: invalid,
          });
        });
      }

      function beginPreviewInteraction(event) {
        const previewState = state.vectorPreview;
        if (!previewIsBoardVisible(previewState)) {
          return false;
        }
        const layout = computeLayout();
        const rect = dom.canvas.getBoundingClientRect();
        const canvasX = event.clientX - rect.left;
        const canvasY = event.clientY - rect.top;
        const hit = previewHandleHit(layout, canvasX, canvasY);
        if (!hit) {
          return false;
        }
        const boardPoint = canvasToBoard(layout, canvasX, canvasY);
        state.previewInteraction = {
          mode: hit.mode,
          pointerId: event.pointerId,
          anchorPoint: boardPoint,
          anchorPlacement: previewDisplayPlacement(previewState),
          anchorBounds: hit.bounds,
          anchorScale: previewDisplayPlacement(previewState)?.scale,
        };
        dom.canvas.setPointerCapture(event.pointerId);
        dom.canvas.style.cursor = hit.mode === 'scale' ? 'nwse-resize' : 'grabbing';
        return true;
      }

      function movePreviewInteraction(event) {
        const interaction = state.previewInteraction;
        if (!interaction) {
          return false;
        }
        const layout = computeLayout();
        const rect = dom.canvas.getBoundingClientRect();
        const boardPoint = canvasToBoard(layout, event.clientX - rect.left, event.clientY - rect.top);
        const previewPlacement = clonePlacement(interaction.anchorPlacement);
        if (!previewPlacement) {
          return false;
        }
        if (interaction.mode === 'drag') {
          updatePreviewPlacement({
            x: previewPlacement.x + (boardPoint.x - interaction.anchorPoint.x),
            y: previewPlacement.y + (boardPoint.y - interaction.anchorPoint.y),
            scale: previewPlacement.scale,
          });
        } else if (interaction.mode === 'scale') {
          const bounds = interaction.anchorBounds;
          const centerX = (bounds.xMin + bounds.xMax) * 0.5;
          const centerY = (bounds.yMin + bounds.yMax) * 0.5;
          const startDist = Math.hypot(
            interaction.anchorPoint.x - centerX,
            interaction.anchorPoint.y - centerY,
          );
          const currentDist = Math.max(1e-6, Math.hypot(boardPoint.x - centerX, boardPoint.y - centerY));
          const ratio = currentDist / Math.max(1e-6, startDist);
          updatePreviewPlacement({
            x: previewPlacement.x,
            y: previewPlacement.y,
            scale: Math.max(0.05, Number(interaction.anchorScale || previewPlacement.scale) * ratio),
          });
        }
        return true;
      }

      function endPreviewInteraction(event) {
        if (!state.previewInteraction) {
          return false;
        }
        try {
          dom.canvas.releasePointerCapture(event.pointerId);
        } catch (_error) {
          // ignore browsers that already released capture
        }
        state.previewInteraction = null;
        dom.canvas.style.cursor = 'default';
        schedulePreviewRefresh();
        return true;
      }

      function drawCurveFitOverlay(layout) {
        const preview = state.vectorPreview;
        const curveFit = state.debugCurveFit;
        if (!preview || preview.sourceType !== 'image' || !curveFit || !curveFit.available) {
          return;
        }

        const overlay = curveFit.overlay_geometry || {};
        const showRaw = Boolean(dom.overlayRawToggle && dom.overlayRawToggle.checked);
        const showCurves = Boolean(dom.overlayCurvesToggle && dom.overlayCurvesToggle.checked);
        const showFallback = Boolean(dom.overlayFallbackToggle && dom.overlayFallbackToggle.checked);
        const colorByType = Boolean(dom.overlayColorToggle && dom.overlayColorToggle.checked);

        if (showRaw) {
          (overlay.raw_contours || []).forEach((stroke) => {
            drawStrokePath(layout, stroke, {
              strokeStyle: 'rgba(127, 140, 153, 0.55)',
              lineWidth: Math.max(0.9, Math.min(2.0, layout.scale * 0.006)),
              alpha: 0.85,
              dashed: true,
            });
          });
        }

        if (showCurves) {
          (overlay.fitted_primitives || []).forEach((primitive) => {
            const sampled = sampleDebugPrimitive(primitive);
            if (sampled.length < 2) {
              return;
            }
            drawStrokePath(layout, sampled, {
              strokeStyle: primitiveColor(primitive.type, colorByType),
              lineWidth: Math.max(1.2, Math.min(2.8, layout.scale * 0.009)),
              alpha: 0.92,
              dashed: false,
            });
          });
        }

        if (showFallback) {
          (overlay.fallback_line_spans || []).forEach((stroke) => {
            drawStrokePath(layout, stroke, {
              strokeStyle: 'rgba(186, 70, 87, 0.96)',
              lineWidth: Math.max(1.8, Math.min(3.8, layout.scale * 0.013)),
              alpha: 1,
              dashed: false,
            });
          });
        }
      }

      function drawPen(layout) {
        if (!state.penPose) {
          return;
        }
        const point = boardToCanvas(layout, state.penPose.x, state.penPose.y);
        ctx.beginPath();
        ctx.arc(point.x, point.y, state.penContact ? 6.4 : 5.2, 0, Math.PI * 2);
        ctx.fillStyle = state.penContact ? '#1a2026' : '#7f8c99';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(point.x, point.y, state.penContact ? 10.5 : 8.4, 0, Math.PI * 2);
        ctx.strokeStyle = state.penContact ? 'rgba(37, 99, 235, 0.68)' : 'rgba(127, 140, 153, 0.42)';
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      function renderBoard() {
        const layout = computeLayout();
        resizeCanvas(layout);
        drawBackground(layout);
        drawBoardBase(layout);
        drawZones(layout);
        drawColumnGuides(layout);
        const rasterSession = state.boardRasterSession;
        if (rasterSession?.active && rasterSession.phase === 'edit') {
          drawBoardRasterSession(layout);
          drawRasterCropOverlay(layout);
          drawBoardEditToolCursor(layout);
          syncBoardEditCanvasOverlay(layout);
        } else {
          drawVectorPreview(layout);
        }
        drawPreviewPlacementControls(layout);
        drawCurveFitOverlay(layout);
        drawTrail(layout);
        drawRobot(layout);
        drawPen(layout);
      }

      function render() {
        renderBoard();
        requestAnimationFrame(render);
      }

      async function previewText({ textOverride } = {}) {
        const text = typeof textOverride === 'string' ? textOverride : dom.textInput.value.trim();
        if (!text.trim()) {
          throw new Error('Enter text before preview.');
        }
        const textOptions = readTextOptions();
        const payload = await apiRequest('/api/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            input_type: 'text',
            text,
            settings: {
              placement: readPlacement(),
              ...textOptions,
            },
          }),
        });
        applyVectorPreview('text', payload, { origin: 'text', boardVisible: false });
      }

      async function drawText() {
        if (!state.currentPreviewId || state.previewDirty) {
          throw new Error('Generate Preview again before drawing.');
        }
        await ensureMode('text');
        const payload = await apiRequest('/api/draw', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            preview_id: state.currentPreviewId,
          }),
        });
        clearNotice();
        hideBoardPreviewAfterDraw(`Text draw published from cached preview ${payload.preview_id}. Board preview hidden so the robot trail is visible.`);
        pushFeed('Text draw published from cached preview.', 'success');
      }

      async function previewSvg() {
        const svg = dom.svgInput.value.trim();
        if (!svg) {
          throw new Error('Paste SVG markup before preview.');
        }
        const payload = await apiRequest('/api/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            input_type: 'svg',
            svg,
            settings: {
              placement: readPlacement(),
            },
          }),
        });
        applyVectorPreview('svg', payload, { origin: 'svg', boardVisible: true });
      }

      async function drawSvg() {
        if (!state.currentPreviewId || state.previewDirty) {
          throw new Error('Generate Preview again before drawing.');
        }
        await ensureMode('draw');
        const payload = await apiRequest('/api/draw', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            preview_id: state.currentPreviewId,
          }),
        });
        clearNotice();
        hideBoardPreviewAfterDraw(`SVG draw published from cached preview ${payload.preview_id}. Board preview hidden so the robot trail is visible.`);
        pushFeed('SVG draw published from cached preview.', 'success');
      }

      function formatSketchBounds(bounds) {
        if (!bounds) {
          return '--';
        }
        const xMin = Number(bounds.x_min);
        const xMax = Number(bounds.x_max);
        const yMin = Number(bounds.y_min);
        const yMax = Number(bounds.y_max);
        if (![xMin, xMax, yMin, yMax].every(Number.isFinite)) {
          return '--';
        }
        return `x ${formatRange(xMin, xMax)} · y ${formatRange(yMin, yMax)}`;
      }

      function formatSketchSafeBounds(metadata) {
        const xMin = Number(metadata.safe_x_min);
        const xMax = Number(metadata.safe_x_max);
        const yMin = Number(metadata.safe_y_min);
        const yMax = Number(metadata.safe_y_max);
        if (![xMin, xMax, yMin, yMax].every(Number.isFinite)) {
          return '--';
        }
        return `x ${formatRange(xMin, xMax)} · y ${formatRange(yMin, yMax)}`;
      }

      function sketchPreviewPointRatio(preview) {
        const returned = Number(preview && preview.returned_point_count);
        const original = Number(preview && preview.original_point_count);
        if (!Number.isFinite(returned) || !Number.isFinite(original) || original <= 0) {
          return { returned: 0, original: 0, percent: 0 };
        }
        return {
          returned,
          original,
          percent: Math.max(0, Math.min(100, (returned / original) * 100)),
        };
      }

      function sketchTruncationWarning(payload) {
        const preview = payload && payload.preview ? payload.preview : {};
        const hasSvg = Boolean(payload && (payload.execution_preview_svg || payload.preview_svg));
        const overlayActive = sketchBoardOverlayActive();
        const ratio = sketchPreviewPointRatio(preview);
        const pointText = ratio.original > 0
          ? `Sampled points ${ratio.returned}/${ratio.original} (${ratio.percent.toFixed(1)}%)${preview.truncated ? ' · truncated' : ''}.`
          : 'Sampled point counts unavailable.';
        if (hasSvg && overlayActive) {
          return `Board Workspace is showing the full executable preview path. ${pointText} Sampled points are debug only and are not drawn as a separate board layer.`;
        }
        if (hasSvg) {
          return `Executable preview is loading on the Board Workspace. ${pointText} Sampled points are debug only and are not drawn as a separate board layer.`;
        }
        if (preview.truncated) {
          return `Executable preview SVG is unavailable. ${pointText}`;
        }
        return `Executable preview SVG is unavailable. ${pointText}`;
      }

      function formatSketchTiming(timing) {
        if (!timing || typeof timing !== 'object') {
          return 'timing unavailable';
        }
        const stagePairs = [
          ['Decode', 'decode_time_ms'],
          ['Resize', 'resize_time_ms'],
          ['Normalize', 'normalize_time_ms'],
          ['Threshold', 'threshold_time_ms'],
          ['Cleanup', 'cleanup_time_ms'],
          ['Skeletonize', 'skeleton_time_ms'],
          ['Trace', 'trace_time_ms'],
          ['Simplify', 'simplify_time_ms'],
          ['Merge', 'merge_time_ms'],
          ['Curve Fit', 'curve_fit_time_ms'],
          ['Scale', 'scale_time_ms'],
          ['Total', 'preview_total_time_ms'],
        ];
        const rendered = stagePairs.map(([label, key]) => {
          const value = Number(timing[key]);
          return `✓ ${label} ${Number.isFinite(value) ? value.toFixed(0) : '--'} ms`;
        });
        const slowest = timing.slowest_stage || {};
        const slowStage = String(slowest.stage || '');
        const slowMs = Number(slowest.time_ms);
        let hint = '';
        if (slowStage === 'skeleton') {
          hint = ' · hint lower max dim';
        } else if (slowStage === 'merge') {
          hint = ' · hint use Raw/Detail or lower merge gap';
        } else if (slowStage === 'curve_fit') {
          hint = ' · hint increase tolerance or turn smoothness off';
        }
        const slowText = slowStage
          ? `slowest ${slowStage} ${Number.isFinite(slowMs) ? slowMs.toFixed(0) : '--'} ms${hint}`
          : 'slowest --';
        return `${slowText}\n${rendered.join(' · ')}`;
      }

      function formatCount(value) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) {
          return '--';
        }
        return Math.round(numeric).toLocaleString();
      }

      function formatMeters(value) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) {
          return '--';
        }
        return `${numeric.toFixed(2)} m`;
      }

      function formatMillis(value) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) {
          return '--';
        }
        return `${numeric.toFixed(0)} ms`;
      }

      function shortHash(value) {
        const text = String(value || '');
        return text ? text.slice(0, 10) : '--';
      }

      function metricTile(label, value) {
        return (
          '<div class="preview-metric">' +
            `<div class="label">${escapeHtml(label)}</div>` +
            `<div class="value">${escapeHtml(value)}</div>` +
          '</div>'
        );
      }

      function renderPreviewMetrics(payload) {
        if (!dom.previewMetricsGrid) {
          return;
        }
        if (!payload) {
          dom.previewMetricsGrid.innerHTML = '';
          return;
        }
        const metadata = payload.metadata || {};
        const metrics = payload.metrics || {};
        const preview = payload.preview || {};
        const timing = metadata.timing || {};
        const canonicalGeometry = metrics.canonical_geometry || {};
        const executableGeometry = metrics.executable_geometry || {};
        const optimizer = metrics.optimizer || {};
        const tinyDetected = Number(metrics.tiny_details_detected || 0);
        const tinyExpanded = Number(metrics.tiny_details_expanded || 0);
        const tinySizeMm = Number(metrics.minimum_drawable_feature_m || 0) * 1000;
        const tiles = [
          metricTile('Paths', `${formatCount(executableGeometry.draw_path_count || metrics.draw_path_count || payload.stroke_count)} draw`),
          metricTile('Samples', `${formatCount(executableGeometry.sampled_point_count || metrics.draw_sample_count || payload.point_count)} points`),
          metricTile('Tiny Details', `${formatCount(tinyExpanded)}/${formatCount(tinyDetected)} expanded${Number.isFinite(tinySizeMm) && tinySizeMm > 0 ? ` · ${tinySizeMm.toFixed(1)} mm` : ''}`),
          metricTile('Canonical Geometry', `${formatCount(canonicalGeometry.total_curve_count || 0)} curves · ${formatCount(canonicalGeometry.line_count || metadata.line_primitive_count || 0)} lines`),
          metricTile('Draw Length', formatMeters(metrics.draw_length_m)),
          metricTile('Travel', formatMeters(metrics.travel_length_m)),
          metricTile('Optimizer', `${optimizer.used || (metrics.optimized ? 'internal' : 'none')}${optimizer.requested && optimizer.requested !== optimizer.used ? ` · requested ${optimizer.requested}` : ''}`),
          metricTile('Total Time', formatMillis(timing.preview_total_time_ms)),
          metricTile('Primitive Hash', shortHash(payload.primitive_hash)),
        ];
        if (preview.truncated) {
          tiles.push(metricTile('Debug Preview', `${formatCount(preview.returned_point_count)}/${formatCount(preview.original_point_count)} sampled`));
        }
        dom.previewMetricsGrid.innerHTML = tiles.join('');
      }

      function downloadCurrentMetrics() {
        if (!state.currentMetrics) {
          handleError('Download metrics failed', new Error('No preview metrics are available.'));
          return;
        }
        const payload = {
          preview_id: state.currentPreviewId,
          canonical_hash: state.currentCanonicalHash,
          primitive_hash: state.currentPrimitiveHash,
          execution_hash: state.currentExecutionHash,
          settings_hash: state.currentSettingsHash,
          pipeline_mode: state.currentPipelineMode,
          metrics: state.currentMetrics,
        };
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `preview-metrics-${state.currentPreviewId || 'current'}.json`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
      }

      function sketchDiagnosticsText(payload) {
        const metadata = payload.metadata || {};
        const preview = payload.preview || {};
        const valueOrFallback = (value, fallback) => (
          value === null || value === undefined ? fallback : value
        );
        const threshold = Number(valueOrFallback(metadata.effective_threshold_value, metadata.threshold_value));
        const lineSensitivity = Number(metadata.line_sensitivity);
        const scalePercent = Number(metadata.scale_percent);
        const centerX = Number(metadata.center_x_m);
        const centerY = Number(metadata.center_y_m);
        const simplify = Number(valueOrFallback(metadata.effective_simplify_epsilon_px, metadata.simplify_epsilon_px));
        const mergeGap = Number(valueOrFallback(metadata.effective_merge_gap_px, metadata.merge_gap_px));
        const mergeAngle = Number(valueOrFallback(metadata.effective_merge_max_angle_deg, metadata.merge_max_angle_deg));
        const minStroke = Number(valueOrFallback(metadata.effective_min_stroke_length_px, metadata.min_stroke_length_px));
        const curveTolerancePx = Number(metadata.curve_tolerance_px);
        const curveToleranceM = Number(metadata.curve_tolerance_m);
        const curveCount = Number(metadata.curve_primitive_count || 0);
        const lineCount = Number(metadata.line_primitive_count || 0);
        const extractionMethod = metadata.sketch_extraction_method || metadata.threshold_method || '--';
        const optimizer = (payload.metrics && payload.metrics.optimizer) || {};
        const warnings = Array.isArray(payload.warnings) && payload.warnings.length > 0
          ? ` · warnings ${payload.warnings.length}`
          : '';
        const lines = [
          `Executable preview is generated from the same cached primitive payload Draw publishes.`,
          `Pipeline ${payload.pipeline_mode || 'sketch_autotrace'} · safe fit ${metadata.fit_to_safe_area === false ? 'off' : 'on'} · bounds ${formatSketchBounds(payload.bounds)}`,
          `Vectorization ${metadata.vectorization_method || '--'} · optimizer ${optimizer.used || '--'} · scale ${Number.isFinite(scalePercent) ? scalePercent.toFixed(0) : '--'}%`,
          `Debug sampled preview ${Number(preview.returned_point_count || 0)}/${Number(preview.original_point_count || 0)} pts${preview.truncated ? ' · truncated' : ''}; Draw uses the full cached backend plan.`,
          `Robot-safe ${formatSketchSafeBounds(metadata)}${warnings}`,
          formatSketchTiming(metadata.timing).split('\n')[0],
        ];
        return lines.join('\n');
      }

      function updateSketchPreviewDiagnostics(payload = state.sketchPreviewPayload) {
        if (!payload) {
          return;
        }
        const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
        const hasSvg = Boolean(payload.execution_preview_svg || payload.preview_svg);
        const warning = !hasSvg
          ? 'Executable preview SVG is unavailable. Draw still requires a valid cached preview.'
          : (warnings.length ? warnings.join(' · ') : '');
        if (dom.sketchPreviewWarning) {
          dom.sketchPreviewWarning.textContent = warning;
          dom.sketchPreviewWarning.classList.toggle('active', Boolean(warning));
        }
        renderPreviewMetrics(payload);
        if (dom.sketchPreviewDiag) {
          dom.sketchPreviewDiag.textContent = sketchDiagnosticsText(payload);
        }
      }

      function setSketchPreviewInlineStatus(kind, message) {
        if (!dom.sketchPreviewBox || !dom.sketchPreviewImg || !dom.sketchPreviewDiag) {
          return;
        }
        resetSketchDrawState(
          kind === 'loading'
            ? 'Preparing backend-owned Sketch preview. Drawing is disabled until preview succeeds.'
            : 'Draw is unavailable until Generate Preview succeeds.'
        );
        if (state.sketchPreviewUrl) {
          URL.revokeObjectURL(state.sketchPreviewUrl);
          state.sketchPreviewUrl = null;
        }
        state.sketchPreviewSvgText = '';
        state.sketchPreviewPayload = null;
        clearSketchBoardOverlay();
        dom.sketchPreviewImg.removeAttribute('src');
        dom.sketchPreviewImg.style.display = 'none';
        if (dom.sketchPreviewWarning) {
          dom.sketchPreviewWarning.textContent = '';
          dom.sketchPreviewWarning.classList.remove('active');
        }
        if (dom.previewMetricsGrid) {
          dom.previewMetricsGrid.innerHTML = '';
        }
        if (dom.sketchPreviewTitle) {
          dom.sketchPreviewTitle.textContent = 'Executable Preview';
        }
        if (dom.sketchPreviewNote) {
          dom.sketchPreviewNote.textContent = 'Generated from runtime-sampled executable geometry.';
        }
        if (dom.sketchOpenSvgBtn) {
          dom.sketchOpenSvgBtn.disabled = true;
        }
        if (dom.sketchDownloadSvgBtn) {
          dom.sketchDownloadSvgBtn.disabled = true;
        }
        dom.sketchPreviewDiag.textContent = message;
        dom.sketchPreviewBox.classList.add('active');
        if (kind === 'loading') {
          dom.previewChip.innerHTML = '<strong>Preview</strong> sketch · loading';
          dom.summaryPreview.textContent = message;
        }
      }

      function renderSketchPreviewSvg(payload) {
        if (!dom.sketchPreviewBox || !dom.sketchPreviewImg || !dom.sketchPreviewDiag) {
          return;
        }
        if (state.sketchPreviewUrl) {
          URL.revokeObjectURL(state.sketchPreviewUrl);
          state.sketchPreviewUrl = null;
        }
        const svgText = String(payload.execution_preview_svg || payload.preview_svg || '');
        state.sketchPreviewSvgText = svgText;
        state.sketchPreviewPayload = payload;
        if (svgText) {
          const blob = new Blob([svgText], { type: 'image/svg+xml' });
          state.sketchPreviewUrl = URL.createObjectURL(blob);
          dom.sketchPreviewImg.src = state.sketchPreviewUrl;
          dom.sketchPreviewImg.style.display = '';
          prepareSketchBoardOverlay();
        } else {
          dom.sketchPreviewImg.removeAttribute('src');
          dom.sketchPreviewImg.style.display = 'none';
          clearSketchBoardOverlay();
        }
        const modeLabel = pipelineDisplayName(payload);
        if (dom.sketchPreviewTitle) {
          dom.sketchPreviewTitle.textContent = `Executable Preview · ${modeLabel}`;
        }
        if (dom.sketchPreviewNote) {
          dom.sketchPreviewNote.textContent = 'This SVG is generated from the executable sampled geometry that Draw will publish.';
        }
        if (dom.sketchOpenSvgBtn) {
          dom.sketchOpenSvgBtn.disabled = !state.sketchPreviewUrl;
        }
        if (dom.sketchDownloadSvgBtn) {
          dom.sketchDownloadSvgBtn.disabled = !state.sketchPreviewUrl;
        }
        updateSketchPreviewDiagnostics(payload);
        dom.sketchPreviewBox.classList.add('active');
      }

      function applySketchCenterlinePreview(payload, { revectorizeOnly = false } = {}) {
        const preview = payload.preview || {};
        state.sketchPreviewId = payload.preview_id || null;
        state.currentPreviewId = payload.preview_id || null;
        state.currentCanonicalHash = payload.canonical_hash || null;
        state.currentPrimitiveHash = payload.primitive_hash || null;
        state.currentExecutionHash = payload.execution_hash || null;
        state.currentSettingsHash = payload.settings_hash || null;
        state.currentExecutionPreviewSvg = String(payload.execution_preview_svg || '');
        state.currentMetrics = payload.metrics || null;
        state.currentInput = payload.input_type || payload.detected_input_type || 'sketch_image';
        state.currentProcessingSettings = payload.draw_request || {};
        state.currentPipelineMode = payload.pipeline_mode || 'sketch_autotrace';
        state.previewDirty = false;
        const metadata = payload.metadata || {};
        const placement = clonePlacement({
          x: Number(metadata.center_x_m ?? metadata.requested_center_x_m ?? 0),
          y: Number(metadata.center_y_m ?? metadata.requested_center_y_m ?? 0),
          scale: Math.max(0.01, Number(metadata.scale_percent ?? 100) / 100),
        }) || { x: 0, y: 0, scale: 1 };
        const wrappedPreview = {
          strokes: Array.isArray(preview.strokes) ? preview.strokes : [],
          stroke_count: Number(payload.stroke_count || 0),
          point_count: Number(payload.point_count || 0),
          bounds: payload.bounds || null,
          can_draw: true,
          validation_error: 'Use Draw for the backend cached executable payload.',
          diagnostics: {
            sketch_image: {
              canonical_command_count: Number(payload.canonical_command_count || 0),
              metadata: payload.metadata || {},
              metrics: payload.metrics || {},
              preview,
              warnings: payload.warnings || [],
            },
          },
        };
        revokePreviewResources(state.vectorPreview);
        state.vectorPreview = {
          sourceType: 'sketch_image',
          preview: wrappedPreview,
          drawRequest: null,
          origin: 'sketch_image',
          boardVisible: true,
          basePlacement: placement,
          displayPlacement: placement,
          rasterOverlay: null,
        };
        renderSketchPreviewSvg(payload);
        if (dom.pipelineModeLabel) {
          dom.pipelineModeLabel.textContent = pipelineDisplayName(payload);
        }
        const metrics = payload.metrics || {};
        const drawPathCount = Number(metrics.draw_path_count || payload.stroke_count || 0);
        const drawSampleCount = Number(metrics.draw_sample_count || payload.point_count || 0);
        dom.previewChip.innerHTML = `<strong>Preview</strong> ${escapeHtml(payload.input_type || 'sketch_image')} · ${drawPathCount} paths`;
        const drawAvailability = state.sketchPreviewId
          ? 'Draw is available and will publish the same cached primitive payload shown in the board preview.'
          : 'Draw is unavailable because the backend did not return a preview_id.';
        dom.summaryPreview.textContent = `${pipelineDisplayName(payload)} executable preview loaded with ${drawPathCount} drawable paths and ${drawSampleCount} sampled points. Board Workspace shows the full execution_preview_svg path the robot will follow. ${drawAvailability}`;
        setSketchDrawStatus(drawAvailability);
        setNotice('success', 'Sketch preview ready', 'Board Workspace shows the executable path. Draw will publish the cached primitive payload.');
        pushFeed(`${pipelineDisplayName(payload)} preview generated without robot publishing.`, 'success');
        refreshUiState({ redrawBoard: true });
        applyPreprocessPreview(payload.preprocess_preview || null, { revectorizeOnly });
        syncBoardFabState();
      }

      async function uploadAndPreviewFile(file) {
        await previewSketchCenterlineFile(file);
      }

      // Maps AI target resolution slider position (0..4) to longest-edge px for SwinIR/Informative.
      const IMAGE_TARGET_RESOLUTION_STEPS = [512, 768, 1024, 1536, 2048];
      const GOOGLE_API_KEY_STORAGE = 'wall_climber_google_api_key';
      const DEFAULT_NANO_BANANA_PROMPT = (
        'convert to clean black and white line art, coloring book style, '
        + 'minimal details, no shading'
      );

      function readStoredGoogleApiKey() {
        try {
          return String(window.localStorage.getItem(GOOGLE_API_KEY_STORAGE) || '').trim();
        } catch (_error) {
          return '';
        }
      }

      function persistGoogleApiKey(value) {
        try {
          const trimmed = String(value || '').trim();
          if (trimmed) {
            window.localStorage.setItem(GOOGLE_API_KEY_STORAGE, trimmed);
          } else {
            window.localStorage.removeItem(GOOGLE_API_KEY_STORAGE);
          }
        } catch (_error) {
          // Ignore storage failures.
        }
      }

      function syncGoogleApiKeyFieldFromStorage() {
        if (!dom.imageGoogleApiKey) {
          return;
        }
        const stored = readStoredGoogleApiKey();
        if (stored && !dom.imageGoogleApiKey.value) {
          dom.imageGoogleApiKey.value = stored;
        }
      }
      let revectorizeDebounceTimer = null;

      function readImagePreprocessMode() {
        const active = dom.imagePreprocessModeButtons.find((button) => button.classList.contains('active'));
        const mode = active ? String(active.dataset.imageMode || 'photo') : 'photo';
        return mode === 'coloring_book' ? 'coloring_book' : 'photo';
      }

      function resolveImageTargetResolution() {
        const index = Number(dom.imageTargetResolution ? dom.imageTargetResolution.value : 2);
        const clamped = Math.max(0, Math.min(IMAGE_TARGET_RESOLUTION_STEPS.length - 1, index));
        return IMAGE_TARGET_RESOLUTION_STEPS[clamped];
      }

      function syncImagePreprocessUi() {
        const mode = readImagePreprocessMode();
        const rawPrint = Boolean(dom.imageRawPrint && dom.imageRawPrint.checked);
        const aiDisabled = mode === 'coloring_book' && rawPrint;
        const isPhoto = mode === 'photo';
        if (dom.imageRawPrintField) {
          dom.imageRawPrintField.hidden = mode !== 'coloring_book';
          if (mode === 'photo' && dom.imageRawPrint) {
            dom.imageRawPrint.checked = false;
          }
        }
        if (dom.imagePhotoLineartModelField) {
          dom.imagePhotoLineartModelField.hidden = !isPhoto;
          dom.imagePhotoLineartModelField.classList.toggle('field-disabled', !isPhoto);
          const modelValue = String(dom.imagePhotoLineartModel?.value || 'informative').toLowerCase();
          const usesNanoBanana = modelValue === 'nano_banana';
          const usesAnilines = modelValue.startsWith('anilines');
          if (dom.imageGoogleApiKeyField) {
            dom.imageGoogleApiKeyField.hidden = !isPhoto || !usesNanoBanana;
          }
          if (dom.imageNanoBananaPromptField) {
            dom.imageNanoBananaPromptField.hidden = !isPhoto || !usesNanoBanana;
          }
          let anilinesHint = dom.imagePhotoLineartModelField.querySelector('.anilines-weights-hint');
          if (usesAnilines && isPhoto && state.anilinesWeightsCached === false) {
            if (!anilinesHint) {
              anilinesHint = document.createElement('div');
              anilinesHint.className = 'field-hint anilines-weights-hint';
              dom.imagePhotoLineartModelField.appendChild(anilinesHint);
            }
            anilinesHint.textContent =
              'AniLines weights are not cached yet. The first preview may download them automatically, or install with pip install gdown.';
            anilinesHint.hidden = false;
          } else if (anilinesHint) {
            anilinesHint.hidden = true;
          }
        }
        if (dom.imageTargetResolutionField) {
          dom.imageTargetResolutionField.classList.toggle('field-disabled', aiDisabled);
        }
        if (dom.imageForceSolidBlackField) {
          const showForceSolid = mode === 'coloring_book';
          dom.imageForceSolidBlackField.hidden = !showForceSolid;
          dom.imageForceSolidBlackField.classList.toggle('field-disabled', !showForceSolid);
        }
        const forceSolidHint = document.getElementById('image-force-solid-black-hint');
        if (forceSolidHint) {
          forceSolidHint.hidden = mode !== 'coloring_book';
        }
        if (dom.imageTargetResolutionReadout && dom.imageTargetResolution) {
          dom.imageTargetResolutionReadout.textContent = `${resolveImageTargetResolution()} px`;
        }
        const aiPanel = document.querySelector('.image-preprocess-panel');
        if (aiPanel) {
          const aiUnavailable = state.aiPreprocessAvailable === false;
          aiPanel.classList.toggle('field-disabled', aiUnavailable);
          if (aiUnavailable && !aiPanel.dataset.warned) {
            aiPanel.dataset.warned = '1';
            pushFeed('AI preprocess unavailable on this server.', 'info');
          }
        }
        if (dom.imageTargetResolutionField && state.cudaAvailable === false) {
          const hint = dom.imageTargetResolutionField.querySelector('.field-hint');
          if (hint && !hint.dataset.cudaHint) {
            hint.dataset.cudaHint = '1';
            hint.textContent += ' CUDA is required for SwinIR and Informative — previews will fail without a GPU.';
          }
        }
      }

      function resetCompareImageStyles() {
        [dom.compareBefore, dom.compareAfter].forEach((image) => {
          if (!image) {
            return;
          }
          image.style.width = '';
          image.style.height = '';
          image.style.left = '';
          image.style.top = '';
          image.style.objectFit = '';
          image.style.objectPosition = '';
          image.style.transform = '';
          image.style.transformOrigin = '';
        });
      }

      function syncCompareImageLayout() {
        if (!dom.compareBefore || !dom.compareAfter) {
          return;
        }
        const beforeW = dom.compareBefore.naturalWidth;
        const beforeH = dom.compareBefore.naturalHeight;
        const afterW = dom.compareAfter.naturalWidth;
        const afterH = dom.compareAfter.naturalHeight;
        if (!beforeW || !beforeH || !afterW || !afterH) {
          return;
        }
        if (beforeW === afterW && beforeH === afterH) {
          return;
        }
        const beforeLong = Math.max(beforeW, beforeH);
        const afterLong = Math.max(afterW, afterH);
        if (!afterLong) {
          return;
        }
        dom.compareAfter.style.transform = `scale(${beforeLong / afterLong})`;
        dom.compareAfter.style.transformOrigin = 'center center';
      }

      function setCompareSlider(value) {
        const percent = Math.max(0, Math.min(100, Number(value)));
        if (dom.compareAfter) {
          dom.compareAfter.style.clipPath = `inset(0 ${100 - percent}% 0 0)`;
        }
        if (dom.compareHandle) {
          dom.compareHandle.style.left = `${percent}%`;
        }
      }

      function bindCompareDrag(scrubber) {
        if (!scrubber || scrubber.dataset.bound === '1') {
          return;
        }
        scrubber.dataset.bound = '1';
        scrubber.addEventListener('pointerdown', (event) => {
          if (!dom.imageComparePanel?.classList.contains('active') || !dom.imageCompareViewport) {
            return;
          }
          event.preventDefault();
          scrubber.setPointerCapture(event.pointerId);
          const rect = dom.imageCompareViewport.getBoundingClientRect();
          const updateFromClientX = (clientX) => {
            const percent = ((clientX - rect.left) / Math.max(1, rect.width)) * 100;
            setCompareSlider(percent);
          };
          updateFromClientX(event.clientX);
          const onMove = (moveEvent) => {
            moveEvent.preventDefault();
            updateFromClientX(moveEvent.clientX);
          };
          const onUp = (upEvent) => {
            scrubber.releasePointerCapture(upEvent.pointerId);
            scrubber.removeEventListener('pointermove', onMove);
            scrubber.removeEventListener('pointerup', onUp);
            scrubber.removeEventListener('pointercancel', onUp);
          };
          scrubber.addEventListener('pointermove', onMove);
          scrubber.addEventListener('pointerup', onUp);
          scrubber.addEventListener('pointercancel', onUp);
        });
      }

      function openStageCompare(beforeUrl, afterUrl, title, stageMeta = null) {
        if (!dom.imageComparePanel || !beforeUrl || !afterUrl) {
          return;
        }
        let compareReady = false;
        const markCompareReady = () => {
          if (compareReady) {
            return;
          }
          if (!dom.compareBefore?.complete || !dom.compareAfter?.complete) {
            return;
          }
          if (!dom.compareBefore.naturalWidth || !dom.compareAfter.naturalWidth) {
            return;
          }
          compareReady = true;
          resetCompareImageStyles();
          if (
            stageMeta
            && stageMeta.beforeWidthPx
            && stageMeta.beforeHeightPx
            && stageMeta.afterWidthPx
            && stageMeta.afterHeightPx
            && (
              stageMeta.beforeWidthPx !== stageMeta.afterWidthPx
              || stageMeta.beforeHeightPx !== stageMeta.afterHeightPx
            )
          ) {
            syncCompareImageLayout();
          } else if (
            dom.compareBefore.naturalWidth !== dom.compareAfter.naturalWidth
            || dom.compareBefore.naturalHeight !== dom.compareAfter.naturalHeight
          ) {
            syncCompareImageLayout();
          }
          setCompareSlider(50);
        };
        resetCompareImageStyles();
        dom.compareBefore.onload = markCompareReady;
        dom.compareAfter.onload = markCompareReady;
        dom.compareBefore.src = beforeUrl;
        dom.compareAfter.src = afterUrl;
        dom.compareBefore.draggable = false;
        dom.compareAfter.draggable = false;
        if (dom.imageCompareTitle) {
          dom.imageCompareTitle.textContent = title || 'Stage Compare';
        }
        dom.imageComparePanel.classList.add('active');
        bindCompareDrag(dom.compareScrubber);
        markCompareReady();
      }

      function applyPreprocessPreview(preprocessPreview, { revectorizeOnly = false } = {}) {
        state.preprocessPreview = preprocessPreview || null;
        if (!preprocessPreview || !Array.isArray(preprocessPreview.pipeline_stages)) {
          if (dom.pipelineStrip) {
            dom.pipelineStrip.classList.remove('active');
            dom.pipelineStripRow.innerHTML = '';
          }
          if (dom.imageComparePanel) {
            dom.imageComparePanel.classList.remove('active');
          }
          return;
        }
        const stages = preprocessPreview.pipeline_stages;
        if (dom.pipelineStrip && dom.pipelineStripRow) {
          dom.pipelineStrip.classList.add('active');
          dom.pipelineStripRow.innerHTML = stages.map((stage, index) => {
            const arrow = index > 0 ? '<span class="pipeline-arrow">→</span>' : '';
            return (
              `${arrow}` +
              `<button type="button" class="pipeline-stage${index === stages.length - 1 ? ' active' : ''}"` +
              ` data-stage-index="${index}">` +
              `<img src="${stage.data_url}" alt="${escapeHtml(stage.label)}">` +
              `<span class="pipeline-stage-label">${escapeHtml(stage.label)}</span>` +
              `</button>`
            );
          }).join('');
          dom.pipelineStripRow.querySelectorAll('.pipeline-stage').forEach((button) => {
            button.addEventListener('click', () => {
              const index = Number(button.dataset.stageIndex || 0);
              dom.pipelineStripRow.querySelectorAll('.pipeline-stage').forEach((node) => {
                node.classList.toggle('active', node === button);
              });
              const stage = stages[index];
              const beforeStage = index > 0 ? stages[index - 1] : stage;
              const before = index > 0 ? beforeStage.data_url : stage.data_url;
              const after = stage.data_url;
              openStageCompare(
                before,
                after,
                index > 0
                  ? `${beforeStage.label} → ${stage.label}`
                  : `${stage.label}`,
                {
                  beforeWidthPx: Number(beforeStage.width_px || 0),
                  beforeHeightPx: Number(beforeStage.height_px || 0),
                  afterWidthPx: Number(stage.width_px || 0),
                  afterHeightPx: Number(stage.height_px || 0),
                },
              );
            });
          });
          const skipAutoCompare = revectorizeOnly || Boolean(preprocessPreview.reused_from_cache);
          if (!skipAutoCompare) {
            const lastStage = stages[stages.length - 1];
            const beforeLast = stages.length > 1 ? stages[stages.length - 2] : stages[0];
            openStageCompare(
              beforeLast.data_url,
              lastStage.data_url,
              stages.length > 1
                ? `${beforeLast.label} → ${lastStage.label}`
                : `${lastStage.label}`,
              {
                beforeWidthPx: Number(beforeLast.width_px || 0),
                beforeHeightPx: Number(beforeLast.height_px || 0),
                afterWidthPx: Number(lastStage.width_px || 0),
                afterHeightPx: Number(lastStage.height_px || 0),
              },
            );
          }
        }
        const timing = preprocessPreview.timing_ms || {};
        const timingParts = Object.entries(timing)
          .filter(([key]) => key.endsWith('_ms'))
          .map(([key, value]) => `${key.replace('_ms', '')}: ${Number(value).toFixed(0)}ms`);
        if (timingParts.length) {
          pushFeed(`AI preprocess timing — ${timingParts.join(', ')}`, 'info');
        }
        if (preprocessPreview.swinir_backend === 'spandrel_cuda') {
          const info = preprocessPreview.swinir_info ? ` (${preprocessPreview.swinir_info})` : '';
          pushFeed(`SwinIR GPU active${info}`, 'success');
        }
        if (preprocessPreview.informative_backend === 'torch_cuda') {
          const info = preprocessPreview.informative_info ? ` (${preprocessPreview.informative_info})` : '';
          pushFeed(`Informative GPU active${info}`, 'success');
        }
        if (preprocessPreview.anilines_backend === 'torch_cuda') {
          const info = preprocessPreview.anilines_info ? ` (${preprocessPreview.anilines_info})` : '';
          pushFeed(`AniLines GPU active${info}`, 'success');
        }
        (preprocessPreview.warnings || []).forEach((warning) => {
          pushFeed(String(warning), 'error');
        });
      }

      function readFilePreviewSettings() {
        const settings = {
          vectorization_method: String(dom.sketchVectorizationMethod?.value || 'autotrace'),
          preview_geometry_mode: 'smooth_curves',
          fit_to_safe_area: Boolean(dom.sketchFitSafe.checked),
          optimize_stroke_order: Boolean(dom.sketchOptimizeDraw.checked),
          path_optimizer: (dom.sketchOptimizeDraw && dom.sketchOptimizeDraw.checked) ? 'internal' : 'none',
          curve_fit_time_limit_ms: Math.max(0, Number(dom.sketchCurveFitTimeLimit?.value ?? 3000)),
          autotrace_speckle_strength: Math.max(0, Math.min(5, Number(dom.autotraceSpeckleStrength?.value ?? 1))),
        };
        settings.image_photo_lineart_model = String(dom.imagePhotoLineartModel?.value || 'informative');
        if (settings.image_photo_lineart_model === 'nano_banana') {
          const promptValue = String(dom.imageNanoBananaPrompt?.value || '').trim();
          settings.image_nano_banana_prompt = promptValue || DEFAULT_NANO_BANANA_PROMPT;
          const apiKeyValue = String(dom.imageGoogleApiKey?.value || readStoredGoogleApiKey()).trim();
          if (apiKeyValue) {
            settings.image_google_api_key = apiKeyValue;
          }
        }
        if (DEBUG_MODE) {
          settings.margin_m = Number(dom.sketchMargin?.value || 0.05);
          settings.scale_percent = Math.max(1, Number(dom.sketchScalePercent?.value || 100));
          settings.curve_tolerance_px = Math.max(0.05, Number(dom.sketchCurveTolerance?.value || 0.6));
          if (dom.sketchCenterX?.value.trim() !== '') {
            settings.center_x_m = Number(dom.sketchCenterX.value);
          }
          if (dom.sketchCenterY?.value.trim() !== '') {
            settings.center_y_m = Number(dom.sketchCenterY.value);
          }
        }
        settings.image_preprocess_mode = readImagePreprocessMode();
        settings.image_raw_print = Boolean(dom.imageRawPrint?.checked);
        settings.image_target_resolution = resolveImageTargetResolution();
        settings.image_force_solid_black_lines = (
          readImagePreprocessMode() === 'coloring_book'
          && Boolean(dom.imageForceSolidBlack?.checked)
        );
        return settings;
      }

      async function previewSketchCenterlineFile(file, { revectorizeOnly = false } = {}) {
        if (!file) {
          setSketchPreviewInlineStatus('error', 'Choose an SVG or raster image first.');
          throw new Error('Choose an SVG or raster image first.');
        }
        const generation = bumpSketchPreviewGeneration();
        state.sketchPreviewBusy = true;
        refreshUiState();
        const loadingMessage = revectorizeOnly
          ? 'Re-vectorizing from cached lineart...'
          : 'Generating executable preview...';
        setSketchPreviewInlineStatus('loading', loadingMessage);
        try {
          const form = new FormData();
          form.append('file', file);
          form.append('input_type', 'auto');
          const previewSettings = readFilePreviewSettings();
          form.append('settings_json', JSON.stringify(previewSettings));
          const aiEnabled = previewSettings.image_preprocess_mode
            && !(previewSettings.image_preprocess_mode === 'coloring_book' && previewSettings.image_raw_print);
          if (aiEnabled && !revectorizeOnly) {
            setSketchPreviewInlineStatus('loading', 'Running AI preprocess and vectorizing...');
          } else if (revectorizeOnly) {
            setSketchPreviewInlineStatus('loading', 'Re-vectorizing from cached lineart...');
          }
          const payload = await apiRequest('/api/preview', {
            method: 'POST',
            body: form,
            timeoutMs: aiEnabled ? 300000 : 180000,
          });
          if (generation !== state.sketchPreviewGeneration) {
            return;
          }
          if (file && file.name) {
            dom.fileMeta.innerHTML = `<strong>File</strong> ${escapeHtml(file.name)} · ready`;
          }
          const pipelineMode = String(payload.pipeline_mode || '');
          if (
            pipelineMode === 'sketch_potrace'
            || pipelineMode === 'sketch_autotrace'
            || pipelineMode.startsWith('sketch_ai_')
            || pipelineMode.startsWith('sketch_raw_print_')
          ) {
            applySketchCenterlinePreview(payload, { revectorizeOnly });
            state.previewDirty = false;
            if (payload.preprocess_preview && payload.preprocess_preview.reused_from_cache) {
              pushFeed('Re-vectorized from cached AI lineart.', 'info');
            }
          } else {
            applyVectorPreview(payload.source_type || payload.input_type || 'file', payload, { origin: 'file', boardVisible: true });
          }
        } finally {
          if (generation === state.sketchPreviewGeneration) {
            state.sketchPreviewBusy = false;
          }
          refreshUiState();
        }
      }

      function scheduleAutoRevectorizePreview() {
        const file = dom.fileInput.files && dom.fileInput.files[0];
        if (!file || state.activeTool !== 'file') {
          return;
        }
        if (!state.currentPreviewId && !state.sketchPreviewId) {
          return;
        }
        if (revectorizeDebounceTimer) {
          clearTimeout(revectorizeDebounceTimer);
        }
        revectorizeDebounceTimer = setTimeout(() => {
          revectorizeDebounceTimer = null;
          previewSketchCenterlineFile(file, { revectorizeOnly: true }).catch((error) => {
            handleError('Re-vectorize failed', error);
          });
        }, 300);
      }

      async function previewUploadedFile() {
        const file = dom.fileInput.files && dom.fileInput.files[0];
        await uploadAndPreviewFile(file);
      }

      async function drawUploadedFile() {
        if (!state.currentPreviewId || state.previewDirty) {
          throw new Error('Generate Preview again before drawing.');
        }
        if (state.sketchDrawBusy) {
          return;
        }
        state.sketchDrawBusy = true;
        refreshUiState();
        try {
          await ensureMode('draw');
          const payload = await apiRequest('/api/draw', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              preview_id: state.currentPreviewId,
            }),
          });
          clearNotice();
          hideBoardPreviewAfterDraw(`Draw published from cached preview ${payload.preview_id}. Board preview hidden so the robot trail is visible.`);
          pushFeed('Uploaded file draw published from cached preview.', 'success');
        } finally {
          state.sketchDrawBusy = false;
          refreshUiState();
        }
      }

      function handleError(prefix, error) {
        const message = error && error.message ? error.message : 'Unknown error';
        setNotice('error', prefix, message);
        pushFeed(`${prefix}: ${message}`, 'error');
      }

      function setActiveTool(tool) {
        const previousTool = state.activeTool;
        state.activeTool = tool;
        dom.toolButtons.forEach((button) => {
          button.classList.toggle('active', button.dataset.tool === tool);
        });
        dom.toolTextPanel.classList.toggle('active', tool === 'text');
        dom.toolFilePanel.classList.toggle('active', tool === 'file');
        dom.toolSvgPanel.classList.toggle('active', tool === 'svg');
        if (previousTool === 'text' && tool !== 'text' && LiveVoiceController.isListening()) {
          TextDictationController.onToolChanged(tool);
        }
        syncPlacementLabels();
        if (tool === 'text') {
          syncPlacementDefaults(true);
        } else if (!state.placementTouched) {
          syncPlacementDefaults(true);
        }
        refreshUiState({ redrawBoard: true });
      }

      dom.manualPenButtons.forEach((button) => {
        button.addEventListener('click', async () => {
          try {
            await setManualPenMode(button.dataset.manualPen);
            clearNotice();
          } catch (error) {
            handleError(`Arm test switch to ${button.dataset.manualPen} failed`, error);
          }
        });
      });

      dom.toolButtons.forEach((button) => {
        button.addEventListener('click', () => {
          setActiveTool(button.dataset.tool);
        });
      });

      [dom.placementX, dom.placementY, dom.placementScale].forEach((input) => {
        input.addEventListener('input', () => {
          state.placementTouched = true;
          markPreviewSettingsChanged();
          if (previewIsBoardVisible()) {
            try {
              updatePreviewPlacement(readPlacement(), { syncInputs: false });
            } catch (_error) {
              // Ignore partial numeric edits until inputs become valid again.
            }
          }
        });
      });


      dom.svgInput.addEventListener('input', () => {
        if (state.currentInput === 'svg') {
          markPreviewSettingsChanged();
        }
      });

      [
        dom.sketchFitSafe,
        dom.sketchOptimizeDraw,
        dom.sketchCurveTolerance,
        dom.sketchMargin,
        dom.sketchScalePercent,
        dom.sketchCenterX,
        dom.sketchCenterY,
        dom.sketchCurveFitTimeLimit,
        dom.autotraceSpeckleStrength,
        dom.imagePhotoLineartModel,
        dom.imageGoogleApiKey,
        dom.imageNanoBananaPrompt,
        dom.imageTargetResolution,
        dom.imageForceSolidBlack,
        dom.imageRawPrint,
      ].forEach((input) => {
        if (!input) {
          return;
        }
        input.addEventListener('input', () => {
          syncSketchAdvancedVisibility();
          syncVectorizationMethodUi();
          syncImagePreprocessUi();
          if (
            state.currentPipelineMode === 'sketch_potrace'
            || state.currentPipelineMode === 'sketch_autotrace'
            || state.currentPipelineMode.startsWith('sketch_ai_')
            || state.currentPipelineMode.startsWith('sketch_raw_print_')
            || state.activeTool === 'file'
          ) {
            markPreviewSettingsChanged();
          }
        });
        input.addEventListener('change', () => {
          syncSketchAdvancedVisibility();
          syncVectorizationMethodUi();
          syncImagePreprocessUi();
          if (
            state.currentPipelineMode === 'sketch_potrace'
            || state.currentPipelineMode === 'sketch_autotrace'
            || state.currentPipelineMode.startsWith('sketch_ai_')
            || state.currentPipelineMode.startsWith('sketch_raw_print_')
            || state.activeTool === 'file'
          ) {
            markPreviewSettingsChanged();
          }
        });
      });

      if (dom.sketchVectorizationMethod) {
        dom.sketchVectorizationMethod.addEventListener('change', () => {
          syncVectorizationMethodUi();
          scheduleAutoRevectorizePreview();
        });
      }

      if (dom.imageGoogleApiKey) {
        dom.imageGoogleApiKey.addEventListener('change', () => {
          persistGoogleApiKey(dom.imageGoogleApiKey.value);
        });
      }

        dom.imagePreprocessModeButtons.forEach((button) => {
        button.addEventListener('click', () => {
          dom.imagePreprocessModeButtons.forEach((node) => {
            node.classList.toggle('active', node === button);
          });
          if (readImagePreprocessMode() === 'photo' && dom.imageRawPrint) {
            dom.imageRawPrint.checked = false;
          }
          syncImagePreprocessUi();
          if (state.activeTool === 'file' || String(state.currentPipelineMode || '').startsWith('sketch_')) {
            markPreviewSettingsChanged();
          }
        });
      });

      bindCompareDrag(dom.compareScrubber);

      function isAcceptedSketchFile(file) {
        if (!file) {
          return false;
        }
        const mime = String(file.type || '').toLowerCase();
        const allowedMime = new Set([
          'image/png',
          'image/jpeg',
          'image/webp',
          'image/svg+xml',
        ]);
        if (allowedMime.has(mime)) {
          return true;
        }
        const name = String(file.name || '').toLowerCase();
        return /\.(png|jpe?g|webp|svg)$/.test(name);
      }

      function updateFileDropLabel(file) {
        if (!dom.fileDropName) {
          return;
        }
        dom.fileDropName.textContent = file && file.name ? file.name : '';
      }

      function assignSketchFile(file) {
        if (!dom.fileInput || !file) {
          return;
        }
        const dt = new DataTransfer();
        dt.items.add(file);
        dom.fileInput.files = dt.files;
        updateFileDropLabel(file);
        suppressNextFileChangeClear = true;
        dom.fileInput.dispatchEvent(new Event('change', { bubbles: true }));
      }

      async function acceptSketchFile(file, { autoPreview = false } = {}) {
        if (!isAcceptedSketchFile(file)) {
          handleError('Picture upload failed', new Error('Use PNG, JPG, WebP, or SVG.'));
          return;
        }
        state.lastSketchFile = file;
        if (!autoPreview) {
          clearSketchBoardPreviewForNewFile();
        }
        assignSketchFile(file);
        if (autoPreview) {
          try {
            await previewSketchCenterlineFile(file);
          } catch (error) {
            handleError('Generate preview failed', error);
          }
        }
      }

      function wireFileDropZone() {
        if (!dom.fileDropZone || !dom.fileInput) {
          return;
        }

        const showDrag = (active) => {
          dom.fileDropZone.classList.toggle('drag-over', active);
        };

        dom.fileDropZone.addEventListener('click', (event) => {
          if (event.target === dom.fileInput) {
            return;
          }
          dom.fileInput.click();
        });

        dom.fileDropZone.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            dom.fileInput.click();
          }
        });

        ['dragenter', 'dragover'].forEach((eventName) => {
          dom.fileDropZone.addEventListener(eventName, (event) => {
            event.preventDefault();
            event.stopPropagation();
            showDrag(true);
          });
        });

        ['dragleave', 'drop'].forEach((eventName) => {
          dom.fileDropZone.addEventListener(eventName, (event) => {
            event.preventDefault();
            event.stopPropagation();
            showDrag(false);
          });
        });

        dom.fileDropZone.addEventListener('drop', (event) => {
          const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
          if (!file) {
            return;
          }
          // Do not auto-preview on drop; users may want to Edit on Board first.
          acceptSketchFile(file, { autoPreview: false });
        });

        dom.fileInput.addEventListener('change', () => {
          const file = dom.fileInput.files && dom.fileInput.files[0];
          const skipClear = suppressNextFileChangeClear;
          suppressNextFileChangeClear = false;
          if (file) {
            state.lastSketchFile = file;
          }
          updateFileDropLabel(file || null);
          if (file && !skipClear) {
            clearSketchBoardPreviewForNewFile();
            state.lastSketchFile = file;
          } else if (file) {
            markPreviewSettingsChanged();
            resetSketchDrawState('Draw is unavailable until Generate Preview runs for this file.');
          }
          refreshUiState();
        });
      }

      wireFileDropZone();


      dom.placementResetBtn.addEventListener('click', () => {
        syncPlacementDefaults(true);
        if (previewIsBoardVisible()) {
          try {
            updatePreviewPlacement(readPlacement(), { syncInputs: false });
            schedulePreviewRefresh();
          } catch (_error) {
            // Keep defaults even if the preview route is temporarily unavailable.
          }
        }
        pushFeed('Placement reset for the active tool.', 'info');
      });

      dom.clearPreviewBtn.addEventListener('click', async () => {
        try {
          await clearCurrentPreview();
          clearNotice();
        } catch (error) {
          handleError('Clear preview failed', error);
        }
      });

      dom.clearTrailBtn.addEventListener('click', clearTrail);

      // -- Single-action text flow (Task 22) --
      // commitText: generates a fresh preview and immediately draws it.
      async function commitText() {
        const writePayload = resolveTextWritePayload();
        ensureColumnDrafts();
        const column = writePayload.column || readTextColumn();
        const undoCapture = {
          column,
          trailStartIndex: state.trailSegments.length,
          committedBefore: state.textColumnDrafts[column].committed || '',
        };
        state.pendingWriteUndo = undoCapture;
        state.previewDirty = false;
        clearNotice();
        try {
          await ensureMode('text');
          if (writePayload.resetCursor) {
            await resetBackendTextCursor(writePayload.column, { clearInk: false });
          }
          await previewText({ textOverride: writePayload.text });
          if (!state.currentPreviewId) {
            throw new Error('Preview did not produce a valid preview_id.');
          }
          const payload = await apiRequest('/api/draw', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preview_id: state.currentPreviewId }),
          });
          clearNotice();
          hideBoardPreviewAfterDraw(`Text written from preview ${payload.preview_id}.`);
          state.textColumnDrafts[column].committed = dom.textInput ? dom.textInput.value : '';
          state.textWriteUndoStack[column].push(undoCapture);
          state.pendingWriteUndo = null;
          persistCurrentColumnDraft();
          if (LiveVoiceController.isListening()) {
            LiveVoiceController._textareaBase = dom.textInput ? dom.textInput.value : '';
          }
          const modeLabel = writePayload.mode === 'append' ? 'Appended new text.' : 'Text written successfully.';
          pushFeed(modeLabel, 'success');
        } catch (error) {
          throw error;
        }
      }

      dom.textSubmitBtn.addEventListener('click', async () => {
        try {
          await commitText();
        } catch (error) {
          handleError('Write failed', error);
        }
      });

      dom.textClearBtn.addEventListener('click', () => {
        undoLastWrite().catch((error) => {
          handleError('Undo last write failed', error);
        });
      });

      dom.svgPreviewBtn.addEventListener('click', async () => {
        try {
          await previewSvg();
        } catch (error) {
          handleError('SVG preview failed', error);
        }
      });

      dom.svgSubmitBtn.addEventListener('click', async () => {
        try {
          await drawSvg();
        } catch (error) {
          handleError('SVG draw failed', error);
        }
      });

      dom.svgClearBtn.addEventListener('click', () => {
        dom.svgInput.value = '';
        if (state.currentInput === 'svg') {
          markPreviewSettingsChanged();
        }
      });

      dom.fileUploadBtn.addEventListener('click', async () => {
        const file = dom.fileInput.files && dom.fileInput.files[0];
        if (!file) {
          handleError('Picture upload failed', new Error('Choose an SVG or image file first.'));
          return;
        }
        try {
          await previewSketchCenterlineFile(file);
        } catch (error) {
          handleError('Generate preview failed', error);
        }
      });

      dom.sketchOpenSvgBtn.addEventListener('click', () => {
        if (!state.sketchPreviewUrl) {
          handleError('Open preview failed', new Error('No executable preview is available.'));
          return;
        }
        window.open(state.sketchPreviewUrl, '_blank', 'noopener,noreferrer');
      });

      dom.sketchDownloadSvgBtn.addEventListener('click', () => {
        if (!state.sketchPreviewUrl) {
          handleError('Download preview failed', new Error('No executable preview is available.'));
          return;
        }
        const anchor = document.createElement('a');
        anchor.href = state.sketchPreviewUrl;
        anchor.download = 'execution-preview.svg';
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      });

      dom.sketchDownloadMetricsBtn.addEventListener('click', downloadCurrentMetrics);

      // Task 23 design decision: the file (sketch) panel keeps the two-step
      // preview-then-draw flow instead of a single-action commitSketch().
      // Rationale: users need to inspect the vectorized result and tune
      // sliders (resolution, thin-line filter, extraction method) before
      // committing to an irreversible draw. Collapsing preview+draw into one
      // click would make slider adjustment blind. Text mode does not have
      // this problem because text rendering is deterministic and fast.
      dom.fileDrawBtn.addEventListener('click', async () => {
        try {
          await drawUploadedFile();
        } catch (error) {
          handleError('Draw failed', error);
        }
      });

      if (dom.boardFabClearPreview) {
        dom.boardFabClearPreview.addEventListener('click', async () => {
          try {
            await clearCurrentPreview();
            clearNotice();
          } catch (error) {
            handleError('Clear preview failed', error);
          }
        });
      }
      if (dom.boardFabMain) {
        dom.boardFabMain.addEventListener('click', () => {
          setBoardFabExpanded(!state.boardFabExpanded);
        });
      }
      if (dom.boardFabBlank) {
        dom.boardFabBlank.addEventListener('click', () => {
          const session = state.boardRasterSession;
          if (session?.active && session.source === 'blank' && session.phase === 'edit') {
            clearBoardRasterSession();
            refreshUiState({ redrawBoard: true });
            return;
          }
          startFixedBlankCanvasSession();
        });
      }
      if (dom.boardFabBrushRange) {
        dom.boardFabBrushRange.addEventListener('input', () => {
          applyBoardFabBrushDiameterMm(dom.boardFabBrushRange.value);
        });
      }
      dom.boardFabVectorButtons.forEach((button) => {
        button.addEventListener('click', async () => {
          if (!isRasterEditActive()) {
            return;
          }
          try {
            await setBoardRasterVectorizationMethod(button.dataset.boardVectorMethod);
          } catch (error) {
            handleError('Switch vectorization failed', error);
          }
        });
      });
      if (dom.boardFabPen) {
        dom.boardFabPen.addEventListener('click', () => {
          if (!isRasterEditActive()) {
            return;
          }
          setBoardOverlayMode(null);
          setBoardEditMode(state.boardEditMode === 'pen' ? null : 'pen');
        });
      }
      if (dom.boardFabEraser) {
        dom.boardFabEraser.addEventListener('click', () => {
          if (!isRasterEditActive()) {
            return;
          }
          setBoardOverlayMode(null);
          setBoardEditMode(state.boardEditMode === 'eraser' ? null : 'eraser');
        });
      }
      if (dom.boardFabCrop) {
        dom.boardFabCrop.addEventListener('click', () => {
          if (!isRasterEditActive() || state.boardRasterSession?.phase !== 'edit') {
            return;
          }
          setBoardEditMode(null);
          setBoardOverlayMode(state.boardOverlayMode === 'crop' ? null : 'crop');
        });
      }
      if (dom.boardFabConfirm) {
        dom.boardFabConfirm.addEventListener('click', async () => {
          try {
            await confirmBoardFabAction();
          } catch (error) {
            handleError('Confirm edits failed', error);
          }
        });
      }
      if (dom.boardFabFullscreen) {
        dom.boardFabFullscreen.addEventListener('click', () => {
          toggleBoardFullscreen();
        });
      }

      if (dom.fileEditBoardBtn) {
        dom.fileEditBoardBtn.addEventListener('click', async () => {
          try {
            await startEditOnBoardFromFile();
          } catch (error) {
            handleError('Edit on board failed', error);
          }
        });
      }

      if (dom.boardEditCanvas) {
        dom.boardEditCanvas.addEventListener('pointerdown', (event) => {
          if (!isRasterEditActive() || !state.boardEditMode) {
            return;
          }
          dom.boardEditCanvas.setPointerCapture(event.pointerId);
          state.boardEditLastPoint = null;
          state.boardEditSmoothPoint = null;
          if (state.boardEditMode === 'eraser' || state.boardEditMode === 'pen') {
            updateBoardEditToolPointer(event);
          }
          paintRasterBoardEdit(event);
          event.preventDefault();
        });
        dom.boardEditCanvas.addEventListener('pointermove', (event) => {
          if (!isRasterEditActive() || !state.boardEditMode) {
            return;
          }
          if (state.boardEditMode === 'eraser' || state.boardEditMode === 'pen') {
            updateBoardEditToolPointer(event);
          }
          if (event.buttons === 0) {
            return;
          }
          paintRasterBoardEdit(event);
          event.preventDefault();
        });
        dom.boardEditCanvas.addEventListener('pointerup', () => {
          finalizeRasterBoardStroke();
          refreshUiState({ redrawBoard: true });
        });
        dom.boardEditCanvas.addEventListener('pointercancel', () => {
          finalizeRasterBoardStroke();
          refreshUiState({ redrawBoard: true });
        });
        dom.boardEditCanvas.addEventListener('pointerleave', () => {
          if (state.boardEditMode === 'eraser' || state.boardEditMode === 'pen') {
            state.boardEditToolPointer = null;
          }
        });
      }

      dom.canvas.addEventListener('pointerdown', (event) => {
        if (beginRasterCropInteraction(event)) {
          event.preventDefault();
          return;
        }
        if (beginRasterMoveInteraction(event)) {
          event.preventDefault();
          return;
        }
        if (isRasterEditing()) {
          return;
        }
        if (beginPreviewInteraction(event)) {
          event.preventDefault();
        }
      });

      dom.canvas.addEventListener('pointermove', (event) => {
        if (moveRasterCropInteraction(event)) {
          event.preventDefault();
          refreshUiState({ redrawBoard: true });
          return;
        }
        if (moveRasterMoveInteraction(event)) {
          event.preventDefault();
          refreshUiState({ redrawBoard: true });
          return;
        }
        if (isRasterEditActive()) {
          const layout = computeLayout();
          const rect = dom.canvas.getBoundingClientRect();
          const boardPoint = canvasToBoard(layout, event.clientX - rect.left, event.clientY - rect.top);
          const session = state.boardRasterSession;
          const canMoveRaster = (
            session?.phase === 'edit'
            && !session.fixedBounds
            && !state.boardEditMode
            && !state.boardOverlayMode
          );
          if (state.boardOverlayMode === 'crop' && session?.cropRectBoard && BoardFab.cropHandleHit) {
            const handle = BoardFab.cropHandleHit(session.cropRectBoard, boardPoint);
            dom.canvas.style.cursor = handle === 'move' ? 'grab' : (handle ? 'crosshair' : 'default');
            return;
          }
          if (canMoveRaster && session?.imageBoundsBoard && BoardFab.cropHandleHit) {
            const handle = BoardFab.cropHandleHit(session.imageBoundsBoard, boardPoint);
            dom.canvas.style.cursor = handle === 'move' ? 'grab' : 'default';
            return;
          }
          dom.canvas.style.cursor = 'default';
          return;
        }
        if (movePreviewInteraction(event)) {
          event.preventDefault();
          return;
        }
        if (!previewIsBoardVisible()) {
          dom.canvas.style.cursor = 'default';
          return;
        }
        const layout = computeLayout();
        const rect = dom.canvas.getBoundingClientRect();
        const hit = previewHandleHit(layout, event.clientX - rect.left, event.clientY - rect.top);
        if (!hit) {
          dom.canvas.style.cursor = 'default';
          return;
        }
        dom.canvas.style.cursor = 'grab';
      });

      ['pointerup', 'pointercancel', 'pointerleave'].forEach((eventName) => {
        dom.canvas.addEventListener(eventName, (event) => {
          void (async () => {
            if (endRasterCropInteraction(event)) {
              refreshUiState({ redrawBoard: true });
              event.preventDefault();
              return;
            }
            if (endRasterMoveInteraction(event)) {
              refreshUiState({ redrawBoard: true });
              event.preventDefault();
              return;
            }
            if (endPreviewInteraction(event)) {
              event.preventDefault();
            }
          })();
        });
      });

      dom.canvas.addEventListener('wheel', (event) => {
        if (isRasterEditing() || !previewIsBoardVisible()) {
          return;
        }
        const layout = computeLayout();
        const rect = dom.canvas.getBoundingClientRect();
        const hit = previewHandleHit(layout, event.clientX - rect.left, event.clientY - rect.top);
        if (!hit) {
          return;
        }
        event.preventDefault();
        const placement = previewDisplayPlacement();
        if (!placement) {
          return;
        }
        const multiplier = event.deltaY < 0 ? 1.05 : 0.95;
        updatePreviewPlacement({
          x: placement.x,
          y: placement.y,
          scale: Math.max(0.05, placement.scale * multiplier),
        });
        schedulePreviewRefresh();
      }, { passive: false });

      if (typeof ROSLIB === 'undefined') {
        state.rosbridge = 'error';
        setStatusPill(dom.rosbridgePill, dom.rosbridgeText, 'error', 'Rosbridge · library missing');
      } else {
        try {
          const ros = new ROSLIB.Ros({ url: WS_URL });

          ros.on('connection', () => {
            state.rosbridge = 'connected';
            setStatusPill(dom.rosbridgePill, dom.rosbridgeText, 'connected', 'Rosbridge · connected');
            if (state.reconnectTimer) {
              clearTimeout(state.reconnectTimer);
              state.reconnectTimer = null;
            }
          });

          ros.on('error', () => {
            state.rosbridge = 'error';
            setStatusPill(dom.rosbridgePill, dom.rosbridgeText, 'error', 'Rosbridge · error');
          });

          ros.on('close', () => {
            state.rosbridge = 'disconnected';
            setStatusPill(dom.rosbridgePill, dom.rosbridgeText, 'disconnected', 'Rosbridge · disconnected');
            if (!state.reconnectTimer) {
              state.reconnectTimer = window.setTimeout(() => {
                state.reconnectTimer = null;
                state.rosbridge = 'connecting';
                setStatusPill(dom.rosbridgePill, dom.rosbridgeText, 'connecting', 'Rosbridge · reconnecting');
                ros.connect(WS_URL);
              }, 3000);
            }
          });

          const boardInfoTopic = new ROSLIB.Topic({
            ros,
            name: TOPICS.boardInfo,
            messageType: 'std_msgs/String',
          });
          boardInfoTopic.subscribe((msg) => {
            const parsed = parseBoardInfo(msg.data);
            if (parsed) {
              state.board = parsed;
              if (!state.placementTouched) {
                syncPlacementDefaults(false);
              }
            }
          });

          const robotPoseTopic = new ROSLIB.Topic({
            ros,
            name: TOPICS.robotPose,
            messageType: 'geometry_msgs/Pose2D',
          });
          robotPoseTopic.subscribe((msg) => {
            const x = safeNumber(msg.x);
            const y = safeNumber(msg.y);
            const theta = safeNumber(msg.theta);
            if (x === null || y === null || theta === null) {
              return;
            }
            state.robotPose = { x, y, theta };
          });

          const penPoseTopic = new ROSLIB.Topic({
            ros,
            name: TOPICS.penPose,
            messageType: 'geometry_msgs/PointStamped',
          });
          penPoseTopic.subscribe((msg) => {
            const x = safeNumber(msg.point && msg.point.x);
            const y = safeNumber(msg.point && msg.point.y);
            if (x === null || y === null) {
              return;
            }
            state.penPose = { x, y };
            if (state.penContact) {
              appendTrailPoint({ x, y });
            }
          });

          const penContactTopic = new ROSLIB.Topic({
            ros,
            name: TOPICS.penContact,
            messageType: 'std_msgs/Bool',
          });
          penContactTopic.subscribe((msg) => {
            const nextContact = Boolean(msg.data);
            // We always close the previous segment on a true->false
            // transition. Re-using state.penPose to seed a brand new
            // segment on a false->true transition is a known race
            // hazard: pen_pose_board and pen_contact are independent
            // topics, so the currently-cached pose may still belong
            // to the *previous* stroke (the pen has moved through the
            // air to the new starting point but the new pose has not
            // yet been delivered through rosbridge). Seeding the
            // segment with that stale pose draws a long visible line
            // across an "empty" area that does not exist in the
            // executable plan. Instead we let penPoseTopic.subscribe
            // start the segment naturally on the next pose tick that
            // arrives while penContact is True.
            if (state.penContact && !nextContact) {
              endTrailSegment();
            }
            state.penContact = nextContact;
          });
        } catch (error) {
          state.rosbridge = 'error';
          setStatusPill(dom.rosbridgePill, dom.rosbridgeText, 'error', 'Rosbridge · unavailable');
          pushFeed(`Rosbridge setup failed: ${error.message || error}`, 'error');
        }
      }

      function startRuntimePolling() {
        const poll = async () => {
          try {
            state.backend = 'ready';
            await refreshRuntime();
          } catch (error) {
            state.backend = 'error';
            setStatusPill(dom.backendPill, dom.backendText, 'error', 'Backend · unavailable');
            setStatusPill(dom.runtimePill, dom.runtimeText, 'error', 'Runtime · unavailable');
            setNotice('error', 'Runtime refresh failed', error.message);
            refreshUiState({ redrawBoard: true });
          }
        };
        poll();
        state.runtimeTimer = window.setInterval(poll, RUNTIME_POLL_MS);
      }

      window.addEventListener('resize', () => {
        renderBoard();
        if (isRasterEditActive() && state.boardEditMode) {
          syncBoardEditCanvasOverlay(computeLayout());
        }
      });

      window.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') {
          return;
        }
        if (state.boardFullscreen) {
          toggleBoardFullscreen();
        }
        setBoardOverlayMode(null);
        setBoardEditMode(null);
        syncBoardFabState();
      });

      window.addEventListener('beforeunload', () => {
        if (state.runtimeTimer) {
          clearInterval(state.runtimeTimer);
          state.runtimeTimer = null;
        }
      });

      // ==================================================================
      // Text dictation (text mode only — no voice commands)
      // ==================================================================

      const VOICE_AUTO_WRITE_MS = 3000;
      const VOICE_TARGET_SAMPLE_RATE = 16000;
      const VOICE_WS_PATH = '/api/voice/stream';

      const TextDictationController = {
        _commitTimer: null,
        _autoWriteCancelled: false,

        isTextMode() {
          return state.activeTool === 'text';
        },

        hasPendingAutoWrite() {
          return this._commitTimer !== null;
        },

        cancelAutoWrite(silent = false) {
          if (this._commitTimer) {
            clearTimeout(this._commitTimer);
            this._commitTimer = null;
          }
          this._autoWriteCancelled = true;
          if (!silent) {
            LiveVoiceController.setStatus('Auto-write cancelled — edit the text or press Write.');
            pushFeed('Auto-write cancelled. Edit the text and press Write.', 'info');
          }
        },

        scheduleAutoWrite() {
          if (this._autoWriteCancelled) {
            return;
          }
          if (this._commitTimer) {
            clearTimeout(this._commitTimer);
          }
          LiveVoiceController.setStatus('Listening… auto-write in 3s (press mic to cancel).');
          this._commitTimer = window.setTimeout(async () => {
            this._commitTimer = null;
            if (this._autoWriteCancelled) {
              return;
            }
            const text = dom.textInput ? dom.textInput.value.trim() : '';
            if (!text) {
              LiveVoiceController.setStatus('Listening…');
              return;
            }
            syncActiveColumnDraftFromTextarea();
            LiveVoiceController.setStatus('Writing…');
            try {
              await commitText();
              pushFeed('Dictation written.', 'success');
              LiveVoiceController.resetSession();
            } catch (error) {
              handleError('Dictation write failed', error);
            } finally {
              LiveVoiceController.stopListening({ keepStatus: true });
              LiveVoiceController.setStatus('');
            }
          }, VOICE_AUTO_WRITE_MS);
        },

        onPhraseRecognized(text) {
          const phrase = String(text || '').trim();
          if (!phrase) {
            return;
          }
          this._autoWriteCancelled = false;
          this.scheduleAutoWrite();
        },

        async emergencyStop() {
          this.cancelAutoWrite(true);
          LiveVoiceController.stopListening({ keepStatus: true });
          LiveVoiceController.setStatus('Stopping…');
          endTrailSegment();
          state.penContact = false;
          refreshUiState({ redrawBoard: true });
          try {
            const payload = await apiRequest('/api/emergency/stop', { method: 'POST' });
            if (payload.runtime) {
              state.runtime = payload.runtime;
              state.manualPenMode = payload.runtime.manual_pen_mode || 'auto';
            }
            refreshUiState({ redrawBoard: true });
            pushFeed('Emergency stop: robot halted.', 'success');
          } catch (error) {
            handleError('Emergency stop failed', error);
          } finally {
            LiveVoiceController.setStatus('');
          }
        },

        onToolChanged(tool) {
          if (tool !== 'text' && LiveVoiceController.isListening()) {
            this.cancelAutoWrite(true);
            LiveVoiceController.stopListening({ keepStatus: true });
            LiveVoiceController.setStatus('');
          }
        },
      };

      const LiveVoiceController = {
        _listening: false,
        _socket: null,
        _mediaStream: null,
        _audioContext: null,
        _processor: null,
        _source: null,
        _selectedDeviceId: '',
        _sessionFinal: '',
        _sessionInterim: '',
        _textareaBase: '',
        _streamErrorShown: false,

        isListening() {
          return this._listening;
        },

        fullTranscript() {
          return `${this._sessionFinal}${this._sessionInterim}`.trim();
        },

        resetSession() {
          this._sessionFinal = '';
          this._sessionInterim = '';
          if (this._socket && this._socket.readyState === WebSocket.OPEN) {
            this._socket.send(JSON.stringify({ type: 'control', action: 'reset' }));
          }
        },

        voiceWsUrl() {
          return `${WS_SCHEME}://${WS_HOST}${VOICE_WS_PATH}`;
        },

        supportsMicrophone() {
          return Boolean(navigator.mediaDevices)
            && typeof navigator.mediaDevices.getUserMedia === 'function';
        },

        _audioConstraints() {
          const deviceId = dom.voiceDeviceSelect && dom.voiceDeviceSelect.value
            ? dom.voiceDeviceSelect.value
            : this._selectedDeviceId;
          if (deviceId) {
            return { audio: { deviceId: { exact: deviceId } } };
          }
          return { audio: true };
        },

        setStatus(message) {
          if (dom.voiceStatus) {
            dom.voiceStatus.textContent = message;
          }
        },

        async refreshDevices() {
          if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
            return;
          }
          const devices = await navigator.mediaDevices.enumerateDevices();
          const inputs = devices.filter((device) => device.kind === 'audioinput');
          if (!dom.voiceDeviceSelect) {
            return;
          }
          const previous = dom.voiceDeviceSelect.value;
          dom.voiceDeviceSelect.innerHTML = '<option value="">Default microphone</option>';
          inputs.forEach((device, index) => {
            const option = document.createElement('option');
            option.value = device.deviceId;
            option.textContent = device.label || `Microphone ${index + 1}`;
            dom.voiceDeviceSelect.appendChild(option);
          });
          if (previous && Array.from(dom.voiceDeviceSelect.options).some((opt) => opt.value === previous)) {
            dom.voiceDeviceSelect.value = previous;
          }
          this._selectedDeviceId = dom.voiceDeviceSelect.value;
        },

        toggle() {
          if (!TextDictationController.isTextMode()) {
            this.setStatus('Open the Text tab first.');
            pushFeed('Dictation is only available in the Text tab.', 'info');
            return;
          }
          if (this._listening || TextDictationController.hasPendingAutoWrite()) {
            TextDictationController.cancelAutoWrite();
            this.stopListening();
            return;
          }
          this.startListening().catch((error) => {
            handleError('Dictation failed', error);
          });
        },

        _resampleTo16k(input, inputRate) {
          if (inputRate === VOICE_TARGET_SAMPLE_RATE) {
            return input;
          }
          const ratio = inputRate / VOICE_TARGET_SAMPLE_RATE;
          const outputLength = Math.max(1, Math.floor(input.length / ratio));
          const output = new Float32Array(outputLength);
          for (let index = 0; index < outputLength; index += 1) {
            const srcIndex = index * ratio;
            const left = Math.floor(srcIndex);
            const right = Math.min(left + 1, input.length - 1);
            const frac = srcIndex - left;
            output[index] = input[left] + (input[right] - input[left]) * frac;
          }
          return output;
        },

        _floatTo16BitPcm(float32Array) {
          const buffer = new ArrayBuffer(float32Array.length * 2);
          const view = new DataView(buffer);
          for (let index = 0; index < float32Array.length; index += 1) {
            const sample = Math.max(-1, Math.min(1, float32Array[index]));
            view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
          }
          return buffer;
        },

        _onWsMessage(event) {
          if (typeof event.data !== 'string') {
            return;
          }
          let payload;
          try {
            payload = JSON.parse(event.data);
          } catch (_error) {
            return;
          }
          if (!payload || typeof payload !== 'object') {
            return;
          }
          if (payload.type === 'partial') {
            this._sessionInterim = String(payload.text || '');
            const interim = this._sessionInterim.trim();
            if (dom.textInput && interim) {
              const base = String(this._textareaBase || '').trim();
              dom.textInput.value = base ? `${base} ${interim}` : interim;
              syncActiveColumnDraftFromTextarea();
            }
            this.setStatus(`Heard: ${interim || '…'}`);
            return;
          }
          if (payload.type === 'result') {
            const text = String(payload.text || '').trim();
            if (!text) {
              return;
            }
            const base = String(this._textareaBase || '').trim();
            const merged = base ? `${base} ${text}` : text;
            if (dom.textInput) {
              dom.textInput.value = merged;
            }
            this._textareaBase = merged;
            this._sessionInterim = '';
            syncActiveColumnDraftFromTextarea();
            this.setStatus(`Heard: ${text}`);
            TextDictationController.onPhraseRecognized(text);
            return;
          }
          if (payload.type === 'error' && !this._streamErrorShown) {
            this._streamErrorShown = true;
            this.setStatus(String(payload.message || 'Voice stream error'));
            pushFeed(`Dictation: ${payload.message || 'stream error'}`, 'error');
            this.stopListening();
            return;
          }
          if (payload.type === 'status') {
            this.setStatus(String(payload.message || ''));
          }
        },

        async _connectVoiceStream() {
          return new Promise((resolve, reject) => {
            const ws = new WebSocket(this.voiceWsUrl());
            ws.binaryType = 'arraybuffer';
            let settled = false;
            const finish = (error) => {
              if (settled) {
                return;
              }
              settled = true;
              window.clearTimeout(timer);
              if (error) {
                reject(error);
              } else {
                resolve(ws);
              }
            };
            const timer = window.setTimeout(() => {
              ws.close();
              finish(new Error('Timed out waiting for voice stream (model load can take a minute the first time).'));
            }, 120000);
            ws.onopen = () => {};
            ws.onmessage = (event) => {
              if (typeof event.data !== 'string') {
                return;
              }
              let payload;
              try {
                payload = JSON.parse(event.data);
              } catch (_error) {
                return;
              }
              if (payload.type === 'ready') {
                finish(null);
              } else if (payload.type === 'error') {
                finish(new Error(payload.message || 'Voice unavailable'));
              } else if (payload.type === 'status') {
                LiveVoiceController.setStatus(String(payload.message || 'Loading…'));
              }
            };
            ws.onerror = () => {
              finish(new Error('Voice WebSocket connection failed.'));
            };
            ws.onclose = () => {
              if (!settled) {
                finish(new Error('Voice WebSocket closed before ready.'));
              }
            };
          });
        },

        async startListening() {
          if (!TextDictationController.isTextMode()) {
            this.setStatus('Open the Text tab first.');
            return;
          }
          if (!this.supportsMicrophone()) {
            this.setStatus('Microphone not supported in this browser.');
            pushFeed('Dictation: getUserMedia unavailable.', 'error');
            return;
          }
          if (!window.isSecureContext) {
            this.setStatus('Microphone requires HTTPS or localhost.');
            pushFeed('Dictation: insecure context — microphone blocked.', 'error');
            return;
          }
          this._streamErrorShown = false;
          let ws;
          try {
            ws = await this._connectVoiceStream();
          } catch (error) {
            this.setStatus(String(error.message || error));
            pushFeed(`Dictation: ${error.message || error}`, 'error');
            return;
          }
          try {
            this._releaseMedia();
            this._mediaStream = await navigator.mediaDevices.getUserMedia(this._audioConstraints());
            await this.refreshDevices();
          } catch (err) {
            ws.close();
            this.setStatus('Microphone access denied.');
            pushFeed(`Dictation: microphone denied (${err.message || err}).`, 'error');
            return;
          }

          this._listening = true;
          this._socket = ws;
          persistCurrentColumnDraft();
          this._textareaBase = dom.textInput ? dom.textInput.value : '';
          this.resetSession();
          TextDictationController._autoWriteCancelled = false;
          ws.onmessage = (event) => this._onWsMessage(event);
          ws.onclose = () => {
            if (this._listening) {
              pushFeed('Dictation stream disconnected.', 'info');
              this.stopListening();
            }
          };

          const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
          this._audioContext = new AudioContextCtor({ sampleRate: VOICE_TARGET_SAMPLE_RATE });
          this._source = this._audioContext.createMediaStreamSource(this._mediaStream);
          this._processor = this._audioContext.createScriptProcessor(4096, 1, 1);
          this._processor.onaudioprocess = (event) => {
            if (!this._listening || !this._socket || this._socket.readyState !== WebSocket.OPEN) {
              return;
            }
            const input = event.inputBuffer.getChannelData(0);
            const resampled = this._resampleTo16k(input, this._audioContext.sampleRate);
            this._socket.send(this._floatTo16BitPcm(resampled));
          };
          this._source.connect(this._processor);
          this._processor.connect(this._audioContext.destination);

          if (dom.voiceCaptureBtn) {
            dom.voiceCaptureBtn.classList.add('recording');
            dom.voiceCaptureBtn.setAttribute('aria-pressed', 'true');
          }
          if (dom.voiceCaptureLabel) {
            dom.voiceCaptureLabel.textContent = 'Listening…';
          }
          this.setStatus('Listening… speak into the microphone.');
          pushFeed('Dictation ON — speech appears in the text box.', 'info');
        },

        _releaseMedia() {
          if (this._processor) {
            try {
              this._processor.disconnect();
            } catch (_error) {
              // ignore
            }
            this._processor.onaudioprocess = null;
            this._processor = null;
          }
          if (this._source) {
            try {
              this._source.disconnect();
            } catch (_error) {
              // ignore
            }
            this._source = null;
          }
          if (this._audioContext) {
            this._audioContext.close().catch(() => {});
            this._audioContext = null;
          }
          if (this._mediaStream) {
            this._mediaStream.getTracks().forEach((track) => track.stop());
            this._mediaStream = null;
          }
        },

        stopListening(options = {}) {
          const keepStatus = Boolean(options.keepStatus);
          this._listening = false;
          if (this._socket) {
            try {
              this._socket.onmessage = null;
              this._socket.onclose = null;
              this._socket.close();
            } catch (_error) {
              // ignore
            }
            this._socket = null;
          }
          this._releaseMedia();
          if (dom.voiceCaptureBtn) {
            dom.voiceCaptureBtn.classList.remove('recording');
            dom.voiceCaptureBtn.setAttribute('aria-pressed', 'false');
          }
          if (dom.voiceCaptureLabel) {
            dom.voiceCaptureLabel.textContent = 'Dictate';
          }
          if (!keepStatus) {
            this.setStatus('');
          }
        },
      };

      if (dom.voiceCaptureBtn) {
        dom.voiceCaptureBtn.addEventListener('click', () => {
          LiveVoiceController.toggle();
        });
      }
      if (dom.emergencyStopBtn) {
        dom.emergencyStopBtn.addEventListener('click', () => {
          TextDictationController.emergencyStop();
        });
      }
      if (dom.voiceDeviceSelect) {
        dom.voiceDeviceSelect.addEventListener('change', () => {
          LiveVoiceController._selectedDeviceId = dom.voiceDeviceSelect.value;
          if (LiveVoiceController.isListening()) {
            LiveVoiceController.startListening().catch((err) => {
              handleError('Microphone switch failed', err);
            });
          }
        });
      }
      if (dom.voiceDeviceRefresh) {
        dom.voiceDeviceRefresh.addEventListener('click', () => {
          LiveVoiceController.refreshDevices().catch((err) => {
            handleError('Microphone list refresh failed', err);
          });
        });
      }
      if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
        navigator.mediaDevices.addEventListener('devicechange', () => {
          LiveVoiceController.refreshDevices().catch(() => {});
        });
      }
      if (dom.textInput) {
        dom.textInput.addEventListener('click', () => {
          if (TextDictationController.hasPendingAutoWrite()) {
            TextDictationController.cancelAutoWrite();
          }
          enforceCommittedTextGuard();
        });
        dom.textInput.addEventListener('focus', () => {
          if (TextDictationController.hasPendingAutoWrite()) {
            TextDictationController.cancelAutoWrite(true);
          }
          enforceCommittedTextGuard();
        });
        dom.textInput.addEventListener('select', () => {
          enforceCommittedTextGuard();
        });
        dom.textInput.addEventListener('keydown', (event) => {
          const committed = committedPrefix();
          if (!committed) {
            return;
          }
          const start = dom.textInput.selectionStart;
          const end = dom.textInput.selectionEnd;
          const minCaret = committed.length;
          if (event.key === 'Backspace' && (start < minCaret || (start === end && start <= minCaret))) {
            event.preventDefault();
            enforceCommittedTextGuard();
            return;
          }
          if (event.key === 'Delete' && start < minCaret) {
            event.preventDefault();
            enforceCommittedTextGuard();
            return;
          }
          if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'x' && start < minCaret) {
            event.preventDefault();
            enforceCommittedTextGuard();
          }
        });
        dom.textInput.addEventListener('paste', (event) => {
          const committed = committedPrefix();
          if (!committed) {
            return;
          }
          event.preventDefault();
          const pasted = (event.clipboardData && event.clipboardData.getData('text')) || '';
          if (!pasted) {
            return;
          }
          dom.textInput.value = committed + pasted;
          dom.textInput.setSelectionRange(dom.textInput.value.length, dom.textInput.value.length);
          syncActiveColumnDraftFromTextarea();
        });
        dom.textInput.addEventListener('input', () => {
          enforceCommittedTextGuard();
          syncActiveColumnDraftFromTextarea();
        });
      }
      LiveVoiceController.refreshDevices().catch(() => {});

      if (dom.textLineHeight) {
        dom.textLineHeight.addEventListener('input', () => {
          syncTextLineHeightReadout();
          if (state.currentInput === 'text') {
            markPreviewSettingsChanged();
          }
        });
      }
      if (dom.textColumnSeedGap) {
        dom.textColumnSeedGap.addEventListener('input', () => {
          syncTextColumnSeedGapReadout();
          if (state.currentInput === 'text') {
            markPreviewSettingsChanged();
          }
        });
      }
      if (dom.placementScale) {
        dom.placementScale.addEventListener('input', () => {
          syncTextLineHeightReadout();
          syncTextColumnSeedGapReadout();
        });
      }
      dom.textColumnButtons.forEach((button) => {
        button.addEventListener('click', () => {
          persistCurrentColumnDraft();
          if (LiveVoiceController.isListening()) {
            TextDictationController.cancelAutoWrite(true);
            LiveVoiceController.stopListening({ keepStatus: true });
            LiveVoiceController.setStatus('');
          }
          dom.textColumnButtons.forEach((peer) => {
            peer.classList.toggle('active', peer === button);
          });
          loadColumnDraft(button.dataset.textColumn);
          if (state.currentInput === 'text') {
            markPreviewSettingsChanged();
          }
          refreshUiState({ redrawBoard: true });
          pushFeed(`Text column set to ${button.dataset.textColumn}.`, 'info');
        });
      });
      syncTextLineHeightReadout();
      syncTextColumnSeedGapReadout();

      pushFeed('UI initialized. Waiting for backend and rosbridge.', 'info');
      syncGoogleApiKeyFieldFromStorage();
      syncImagePreprocessUi();
      syncPlacementLabels();
      syncPlacementDefaults(true);
      setTextFontSource('relief_singleline');
      clearVectorPreview(false);
      refreshUiState({ redrawBoard: true });
      startRuntimePolling();
      requestAnimationFrame(render);
    })();
