(function boardFabUtilsModule(global) {
  const CROP_HANDLE_RADIUS_M = 0.012;
  const MIN_CROP_SIZE_M = 0.01;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function normalizeBoardRect(rect) {
    if (!rect) {
      return null;
    }
    return {
      xMin: Math.min(rect.xMin, rect.xMax),
      xMax: Math.max(rect.xMin, rect.xMax),
      yMin: Math.min(rect.yMin, rect.yMax),
      yMax: Math.max(rect.yMin, rect.yMax),
    };
  }

  function boardRectToImageCrop(cropRect, imageBounds) {
    const crop = normalizeBoardRect(cropRect);
    const image = normalizeBoardRect(imageBounds);
    if (!crop || !image) {
      return null;
    }
    const imageWidth = image.xMax - image.xMin;
    const imageHeight = image.yMax - image.yMin;
    if (imageWidth <= 0 || imageHeight <= 0) {
      return null;
    }
    const xMin = clamp((crop.xMin - image.xMin) / imageWidth, 0, 1);
    const xMax = clamp((crop.xMax - image.xMin) / imageWidth, 0, 1);
    const yMin = clamp((crop.yMin - image.yMin) / imageHeight, 0, 1);
    const yMax = clamp((crop.yMax - image.yMin) / imageHeight, 0, 1);
    if ((xMax - xMin) < 0.001 || (yMax - yMin) < 0.001) {
      return null;
    }
    return { xMin, xMax, yMin, yMax };
  }

  function imageCropToPixelRect(normalizedCrop, imageWidth, imageHeight) {
    if (!normalizedCrop) {
      return null;
    }
    const sx = Math.max(0, Math.floor(normalizedCrop.xMin * imageWidth));
    const sy = Math.max(0, Math.floor(normalizedCrop.yMin * imageHeight));
    const sw = Math.max(1, Math.ceil((normalizedCrop.xMax - normalizedCrop.xMin) * imageWidth));
    const sh = Math.max(1, Math.ceil((normalizedCrop.yMax - normalizedCrop.yMin) * imageHeight));
    return { sx, sy, sw, sh };
  }

  function clampRectToBounds(rect, bounds) {
    const normalized = normalizeBoardRect(rect);
    if (!normalized || !bounds) {
      return normalized;
    }
    const width = normalized.xMax - normalized.xMin;
    const height = normalized.yMax - normalized.yMin;
    let xMin = normalized.xMin;
    let yMin = normalized.yMin;
    if (xMin < bounds.x_min) {
      xMin = bounds.x_min;
    }
    if (yMin < bounds.y_min) {
      yMin = bounds.y_min;
    }
    if (xMin + width > bounds.x_max) {
      xMin = bounds.x_max - width;
    }
    if (yMin + height > bounds.y_max) {
      yMin = bounds.y_max - height;
    }
    return {
      xMin,
      xMax: xMin + width,
      yMin,
      yMax: yMin + height,
    };
  }

  function moveRect(rect, dx, dy, bounds) {
    const normalized = normalizeBoardRect(rect);
    if (!normalized) {
      return null;
    }
    return clampRectToBounds({
      xMin: normalized.xMin + dx,
      xMax: normalized.xMax + dx,
      yMin: normalized.yMin + dy,
      yMax: normalized.yMax + dy,
    }, bounds);
  }

  function resizeRect(rect, handle, boardPoint, minSizeM = MIN_CROP_SIZE_M) {
    const base = normalizeBoardRect(rect);
    if (!base || !handle || !boardPoint) {
      return base;
    }
    let { xMin, xMax, yMin, yMax } = base;
    if (handle.includes('w')) {
      xMin = boardPoint.x;
    }
    if (handle.includes('e')) {
      xMax = boardPoint.x;
    }
    if (handle.includes('s')) {
      yMin = boardPoint.y;
    }
    if (handle.includes('n')) {
      yMax = boardPoint.y;
    }
    const next = normalizeBoardRect({ xMin, xMax, yMin, yMax });
    if ((next.xMax - next.xMin) < minSizeM || (next.yMax - next.yMin) < minSizeM) {
      return base;
    }
    return next;
  }

  function cropHandleHit(rect, boardPoint, handleRadiusM = CROP_HANDLE_RADIUS_M) {
    const normalized = normalizeBoardRect(rect);
    if (!normalized || !boardPoint) {
      return null;
    }
    const cx = (normalized.xMin + normalized.xMax) * 0.5;
    const cy = (normalized.yMin + normalized.yMax) * 0.5;
    const handles = [
      { id: 'nw', x: normalized.xMin, y: normalized.yMax },
      { id: 'n', x: cx, y: normalized.yMax },
      { id: 'ne', x: normalized.xMax, y: normalized.yMax },
      { id: 'e', x: normalized.xMax, y: cy },
      { id: 'se', x: normalized.xMax, y: normalized.yMin },
      { id: 's', x: cx, y: normalized.yMin },
      { id: 'sw', x: normalized.xMin, y: normalized.yMin },
      { id: 'w', x: normalized.xMin, y: cy },
    ];
    for (const handle of handles) {
      if (Math.hypot(boardPoint.x - handle.x, boardPoint.y - handle.y) <= handleRadiusM) {
        return handle.id;
      }
    }
    const inside = (
      boardPoint.x >= normalized.xMin
      && boardPoint.x <= normalized.xMax
      && boardPoint.y >= normalized.yMin
      && boardPoint.y <= normalized.yMax
    );
    return inside ? 'move' : null;
  }

  function fitImageBoundsToBoard(imageWidthPx, imageHeightPx, boardBounds, marginM = 0) {
    if (!boardBounds || imageWidthPx <= 0 || imageHeightPx <= 0) {
      return null;
    }
    const availW = Math.max(0.01, (boardBounds.x_max - boardBounds.x_min) - (2 * marginM));
    const availH = Math.max(0.01, (boardBounds.y_max - boardBounds.y_min) - (2 * marginM));
    const scale = Math.min(availW / imageWidthPx, availH / imageHeightPx);
    const widthM = imageWidthPx * scale;
    const heightM = imageHeightPx * scale;
    const cx = (boardBounds.x_min + boardBounds.x_max) * 0.5;
    const cy = (boardBounds.y_min + boardBounds.y_max) * 0.5;
    return {
      xMin: cx - widthM * 0.5,
      xMax: cx + widthM * 0.5,
      yMin: cy - heightM * 0.5,
      yMax: cy + heightM * 0.5,
      scalePercent: 100,
    };
  }

  function composeCropNormalized(parentCrop, innerCrop) {
    const parent = parentCrop || { xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
    if (!innerCrop) {
      return { ...parent };
    }
    const parentWidth = parent.xMax - parent.xMin;
    const parentHeight = parent.yMax - parent.yMin;
    if (parentWidth <= 0 || parentHeight <= 0) {
      return null;
    }
    const next = {
      xMin: parent.xMin + (innerCrop.xMin * parentWidth),
      xMax: parent.xMin + (innerCrop.xMax * parentWidth),
      yMin: parent.yMin + (innerCrop.yMin * parentHeight),
      yMax: parent.yMin + (innerCrop.yMax * parentHeight),
    };
    if ((next.xMax - next.xMin) < 0.001 || (next.yMax - next.yMin) < 0.001) {
      return null;
    }
    return next;
  }

  function effectiveCropPixelSize(memoryWidthPx, memoryHeightPx, cropNormalized) {
    const crop = cropNormalized || { xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
    return {
      width: Math.max(1, memoryWidthPx * (crop.xMax - crop.xMin)),
      height: Math.max(1, memoryHeightPx * (crop.yMax - crop.yMin)),
    };
  }

  function isInkPixel(r, g, b, a) {
    if (a < 16) {
      return false;
    }
    return ((r + g + b) / 3) < 250;
  }

  function inkBoundsPixelRect(canvas, cropNormalized) {
    if (!canvas) {
      return null;
    }
    const pixelRect = imageCropToPixelRect(
      cropNormalized || { xMin: 0, xMax: 1, yMin: 0, yMax: 1 },
      canvas.width,
      canvas.height,
    );
    if (!pixelRect) {
      return null;
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      return null;
    }
    const { sx, sy, sw, sh } = pixelRect;
    const data = ctx.getImageData(sx, sy, sw, sh).data;
    let minX = sw;
    let minY = sh;
    let maxX = -1;
    let maxY = -1;
    for (let y = 0; y < sh; y += 1) {
      for (let x = 0; x < sw; x += 1) {
        const index = ((y * sw) + x) * 4;
        if (isInkPixel(data[index], data[index + 1], data[index + 2], data[index + 3])) {
          minX = Math.min(minX, x);
          minY = Math.min(minY, y);
          maxX = Math.max(maxX, x);
          maxY = Math.max(maxY, y);
        }
      }
    }
    if (maxX < 0 || maxY < 0) {
      return null;
    }
    return {
      xMin: sx + minX,
      yMin: sy + minY,
      xMax: sx + maxX + 1,
      yMax: sy + maxY + 1,
      width: (maxX - minX) + 1,
      height: (maxY - minY) + 1,
    };
  }

  function inkBoundsBoardRect(imageBoundsBoard, inkPixelRect, memoryWidthPx, memoryHeightPx, cropNormalized) {
    const image = normalizeBoardRect(imageBoundsBoard);
    const cropPixel = imageCropToPixelRect(
      cropNormalized || { xMin: 0, xMax: 1, yMin: 0, yMax: 1 },
      memoryWidthPx,
      memoryHeightPx,
    );
    if (!image || !inkPixelRect || !cropPixel || cropPixel.sw <= 0 || cropPixel.sh <= 0) {
      return null;
    }
    const boardWidth = image.xMax - image.xMin;
    const boardHeight = image.yMax - image.yMin;
    const fracXMin = (inkPixelRect.xMin - cropPixel.sx) / cropPixel.sw;
    const fracXMax = (inkPixelRect.xMax - cropPixel.sx) / cropPixel.sw;
    const fracYMin = (inkPixelRect.yMin - cropPixel.sy) / cropPixel.sh;
    const fracYMax = (inkPixelRect.yMax - cropPixel.sy) / cropPixel.sh;
    return {
      xMin: image.xMin + (fracXMin * boardWidth),
      xMax: image.xMin + (fracXMax * boardWidth),
      yMin: image.yMin + (fracYMin * boardHeight),
      yMax: image.yMin + (fracYMax * boardHeight),
    };
  }

  function placementFromInkBounds(
    imageBoundsBoard,
    memoryCanvas,
    fitBounds,
    marginM,
    cropNormalized,
  ) {
    if (!memoryCanvas) {
      return null;
    }
    const inkPixel = inkBoundsPixelRect(memoryCanvas, cropNormalized);
    if (!inkPixel) {
      return null;
    }
    const inkBoard = inkBoundsBoardRect(
      imageBoundsBoard,
      inkPixel,
      memoryCanvas.width,
      memoryCanvas.height,
      cropNormalized,
    );
    if (!inkBoard) {
      return null;
    }
    return placementFromImageBounds(
      inkBoard,
      inkPixel.width,
      inkPixel.height,
      fitBounds,
      marginM,
      { xMin: 0, xMax: 1, yMin: 0, yMax: 1 },
    );
  }

  function placementFromImageBounds(
    imageBoundsBoard,
    memoryWidthPx,
    memoryHeightPx,
    fitBounds,
    marginM,
    cropNormalized,
  ) {
    const bounds = normalizeBoardRect(imageBoundsBoard);
    if (!bounds || !memoryWidthPx || !memoryHeightPx || !fitBounds) {
      return null;
    }
    const effective = effectiveCropPixelSize(memoryWidthPx, memoryHeightPx, cropNormalized);
    const availW = Math.max(1.0e-6, (fitBounds.x_max - fitBounds.x_min) - (2 * marginM));
    const availH = Math.max(1.0e-6, (fitBounds.y_max - fitBounds.y_min) - (2 * marginM));
    const baseScale = Math.min(availW / effective.width, availH / effective.height);
    const baseWidthM = effective.width * baseScale;
    const currentWidthM = bounds.xMax - bounds.xMin;
    return {
      center_x_m: (bounds.xMin + bounds.xMax) * 0.5,
      center_y_m: (bounds.yMin + bounds.yMax) * 0.5,
      scale_percent: Math.max(1, Math.min(500, (currentWidthM / Math.max(1.0e-6, baseWidthM)) * 100)),
      fit_to_safe_area: true,
    };
  }

  function applyNormalizedCropToCanvas(canvas, normalizedCrop) {
    const pixelRect = imageCropToPixelRect(normalizedCrop, canvas.width, canvas.height);
    if (!pixelRect) {
      return false;
    }
    const { sx, sy, sw, sh } = pixelRect;
    const cropped = document.createElement('canvas');
    cropped.width = sw;
    cropped.height = sh;
    cropped.getContext('2d').drawImage(canvas, sx, sy, sw, sh, 0, 0, sw, sh);
    canvas.width = sw;
    canvas.height = sh;
    canvas.getContext('2d').drawImage(cropped, 0, 0);
    return true;
  }

  global.BoardFabUtils = {
    CROP_HANDLE_RADIUS_M,
    MIN_CROP_SIZE_M,
    clamp,
    normalizeBoardRect,
    boardRectToImageCrop,
    imageCropToPixelRect,
    clampRectToBounds,
    moveRect,
    resizeRect,
    cropHandleHit,
    fitImageBoundsToBoard,
    composeCropNormalized,
    effectiveCropPixelSize,
    inkBoundsPixelRect,
    inkBoundsBoardRect,
    placementFromInkBounds,
    placementFromImageBounds,
    applyNormalizedCropToCanvas,
  };
}(typeof window !== 'undefined' ? window : globalThis));
