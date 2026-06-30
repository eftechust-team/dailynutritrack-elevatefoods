from flask import Flask, render_template, redirect, request, abort, send_file, url_for, jsonify
from google.cloud import storage
from google.auth.exceptions import DefaultCredentialsError
import numpy as np
from scipy.optimize import minimize, NonlinearConstraint, nnls, linprog
from scipy import ndimage as ndi
from itertools import combinations
import os
import json
import io
import requests
import re
import csv
import tempfile
import uuid
import struct
import zipfile
import html as _html
import xml.etree.ElementTree as ET
from datetime import datetime
import json as json_lib
from werkzeug.utils import secure_filename
import base64
try:
    from anthropic import Anthropic
except Exception:
    Anthropic = None

# export GOOGLE_APPLICATION_CREDENTIALS="food-ai-455507-e2a9c115814e.json"     
json_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "food-ai-455507-e2a9c115814e.json"))
if os.path.exists(json_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = json_path

# Initialize Claude client for vision-based food classification
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
if ANTHROPIC_API_KEY and Anthropic is not None:
    claude_client = Anthropic(api_key=ANTHROPIC_API_KEY)
else:
    claude_client = None

# Diagnostics toggle (optional): set env DIAG_MODE=1 to include server-side errors in API responses
DIAG_MODE = os.getenv("DIAG_MODE", "0") not in ["0", "false", "False", ""]

# Mesh generation mode and solution limits for memory/time-constrained environments (e.g., Render)
# MESH_MODE: 'all' (default) | 'first' (only first solution) | 'none' (disable STL generation)
MESH_MODE = os.getenv("MESH_MODE", "all").strip().lower()
# MAX_SOLUTIONS: cap how many solution options we compute/return
try:
    MAX_SOLUTIONS = max(1, int(os.getenv("MAX_SOLUTIONS", "2")))
except Exception:
    MAX_SOLUTIONS = 2

# Mesh storage backend: 'gcs' (default) to upload to Google Cloud Storage, or 'local' to keep files in /tmp and serve directly
MESH_STORAGE = os.getenv("MESH_STORAGE", "gcs").strip().lower()

def _user_records_path():
    return os.path.join(tempfile.gettempdir(), "user_records.json")


def _generate_short_user_id(existing_records=None):
    """Generate a short unique user id like usr_a1b2c3d4."""
    records = existing_records or {}
    while True:
        candidate = f"usr_{uuid.uuid4().hex[:8]}"
        if candidate not in records:
            return candidate


def _is_long_legacy_user_id(user_id):
    """Treat legacy UUID-like IDs and very long IDs as candidates for shortening."""
    sid = str(user_id or '').strip()
    if not sid:
        return False
    if re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', sid):
        return True
    return len(sid) > 16


def _normalize_user_record_ids(records):
    """Return records keyed by short IDs, migrating legacy long IDs as needed."""
    if not isinstance(records, dict):
        return {}, False

    normalized = {}
    changed = False

    for key, raw_record in records.items():
        record = raw_record if isinstance(raw_record, dict) else {}
        stored_id = str(record.get('user_id') or key or '').strip()
        target_id = stored_id

        if not target_id or _is_long_legacy_user_id(target_id) or target_id in normalized:
            target_id = _generate_short_user_id(normalized)
            changed = True
        elif key != target_id:
            changed = True

        record['user_id'] = target_id
        normalized[target_id] = record

    return normalized, changed

def _load_user_records():
    try:
        path = _user_records_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    normalized, changed = _normalize_user_record_ids(data)
                    if changed:
                        _save_user_records(normalized)
                    return normalized
    except Exception as e:
        print(f"[WARN] Failed to load user records: {e}")
    return {}

def _save_user_records(records):
    try:
        path = _user_records_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(records, f)
    except Exception as e:
        print(f"[WARN] Failed to save user records: {e}")

def save_user_record(user_id, user_info, daily_nutrition=None, recommendation=None):
    """Upsert one user record and append an optional recommendation history item."""
    records = _load_user_records()
    now = datetime.utcnow().isoformat() + "Z"

    if not user_id:
        user_id = _generate_short_user_id(records)

    existing = records.get(user_id, {
        'user_id': user_id,
        'created_at': now,
        'updated_at': now,
        'user_info': {},
        'history': []
    })

    existing['updated_at'] = now
    existing['user_info'] = user_info or existing.get('user_info', {})

    if daily_nutrition is not None or recommendation is not None:
        existing.setdefault('history', []).append({
            'timestamp': now,
            'daily_nutrition': daily_nutrition or {},
            'recommendation': recommendation or {}
        })

    records[user_id] = existing
    _save_user_records(records)
    return existing

def get_user_record(user_id):
    records = _load_user_records()
    return records.get(user_id)

def _manifest_path():
    import tempfile
    return os.path.join(tempfile.gettempdir(), "meshes_manifest.json")

def _load_manifest():
    try:
        path = _manifest_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to load mesh manifest: {e}")
    return {}

def _save_manifest(manifest):
    try:
        path = _manifest_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f)
    except Exception as e:
        print(f"[WARN] Failed to save mesh manifest: {e}")
 
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = "./static/uploads"
bucket_name = "food-ai"

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".jfif", ".bmp", ".tif", ".tiff"}
DEFAULT_PLATE_DIAMETER_CM = 26.0


def _foodseg_output_dir_from_image_name(image_name):
    base_name = os.path.splitext(os.path.basename(str(image_name or '')))[0]
    return os.path.join('static', 'foodseg', base_name)


def _parts_metadata_path(image_name):
    base_name = os.path.splitext(os.path.basename(str(image_name or '')))[0]
    return os.path.join(_foodseg_output_dir_from_image_name(image_name), f'{base_name}_parts.json')


def _load_parts_metadata(image_name):
    path = _parts_metadata_path(image_name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to load parts metadata: {e}")
        return None


def _save_parts_metadata(image_name, payload):
    try:
        out_dir = _foodseg_output_dir_from_image_name(image_name)
        os.makedirs(out_dir, exist_ok=True)
        with open(_parts_metadata_path(image_name), 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=True, indent=4)
    except Exception as e:
        print(f"[WARN] Failed to save parts metadata: {e}")


def _build_part_context_text(part_contexts):
    if not part_contexts:
        return ''

    lines = []
    for part in part_contexts:
        if not isinstance(part, dict):
            continue
        part_id = int(_safe_float(part.get('part_id', 0), 0))
        food_name = str(part.get('food_name', '') or '').strip() or 'unknown food'
        container_type = str(part.get('container_type', '') or '').strip() or 'unknown container'
        size_cm = round(_safe_float(part.get('container_size_cm', 0.0), 0.0), 2)
        depth_cm = round(_safe_float(part.get('container_depth_cm', 0.0), 0.0), 2)
        fill_ratio = round(_safe_float(part.get('fill_ratio', 0.0), 0.0), 2)
        lines.append(
            f"part_id={part_id}; food={food_name}; container={container_type}; size_cm={size_cm}; depth_cm={depth_cm}; fill_ratio={fill_ratio}"
        )

    if not lines:
        return ''
    return 'User-provided per-food-part container sizing context:\n' + '\n'.join(lines)


def _allowed_image_filename(filename):
    ext = os.path.splitext(str(filename or ''))[1].lower()
    return ext in ALLOWED_IMAGE_EXTENSIONS


def _uploaded_image_extension(file, filename=''):
    """Return a safe image extension for saving an uploaded image."""
    ext = os.path.splitext(str(filename or ''))[1].lower()
    if ext in ALLOWED_IMAGE_EXTENSIONS:
        return ext

    mimetype = str(getattr(file, 'mimetype', '') or '').lower()
    mimetype_map = {
        'image/jpeg': '.jpg',
        'image/jpg': '.jpg',
        'image/png': '.png',
        'image/webp': '.webp',
        'image/bmp': '.bmp',
        'image/tiff': '.tiff',
        'image/x-tiff': '.tiff',
        'image/jfif': '.jfif',
    }
    if mimetype in mimetype_map:
        return mimetype_map[mimetype]
    return ''


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _split_food_hints(raw):
    return [x.strip() for x in re.split(r'[,，;]+', str(raw or '')) if x.strip()]


def _simplify_food_name(raw_name):
    """Strip cooking styles/details and keep a concise food label for lookup/display."""
    name = str(raw_name or '').strip().lower()
    if not name:
        return ''

    # Remove parenthetical notes and punctuation-like separators.
    name = re.sub(r'\([^)]*\)', ' ', name)
    name = re.sub(r'[,/|]+', ' ', name)
    name = name.replace('&', ' and ')

    style_words = {
        'fried', 'stir', 'stirred', 'stir-fried', 'braised', 'grilled', 'roasted',
        'steamed', 'boiled', 'sauteed', 'sautéed', 'baked', 'deep', 'pan', 'seared',
        'spicy', 'sweet', 'sour', 'savory', 'smoked', 'pickled', 'marinated',
        'glazed', 'seasoned', 'crispy', 'crunchy', 'style', 'banchan', 'with',
        'sauce', 'soup', 'stew', 'curry', 'hotpot', 'noodle-soup'
    }
    stop_words = {'the', 'a', 'an', 'of'}

    tokens = [t for t in re.split(r'\s+', name) if t]
    cleaned = [t for t in tokens if t not in style_words and t not in stop_words]
    if not cleaned:
        cleaned = tokens

    simple = ' '.join(cleaned).strip()
    simple = re.sub(r'\s+', ' ', simple)
    return simple


def _csv_row_first_match(food_name):
    if not food_name:
        return None
    found = search_csv_food(food_name)
    if not found:
        return None
    foods = found.get('foods') or []
    if not foods:
        return None
    return foods[0].get('_csv_data')


def _food_profile_from_name(food_name, default_profile=None):
    """Resolve a food to per-gram profile with fallback priority: CSV -> USDA -> Doubao."""
    normalized_name = _simplify_food_name(food_name) or (food_name or '')
    row = _csv_row_first_match(normalized_name) or _csv_row_first_match(food_name)
    if row:
        return {
            'found': True,
            'food_name': row.get('category_name', normalized_name or food_name or 'unknown food'),
            'food_id': int(_safe_float(row.get('category_id', 0), 0)),
            'density': _csv_float_value(row, 'density', default=(default_profile or {}).get('density', 0.95)),
            'calories_pg': _csv_float_value(row, 'calories', default=(default_profile or {}).get('calories_pg', 1.2)),
            'protein_pg': _csv_float_value(row, 'protein', default=(default_profile or {}).get('protein_pg', 0.04)),
            'carbs_pg': _csv_float_value(row, 'carbohydrates', default=(default_profile or {}).get('carbs_pg', 0.13)),
            'fat_pg': _csv_float_value(row, 'fat', default=(default_profile or {}).get('fat_pg', 0.04)),
        }

    # Fallback to USDA -> Doubao through existing resolver when CSV has no match.
    fallback_nutrition, fallback_source = None, ''
    try:
        fallback_nutrition, fallback_source = get_food_nutrition_with_fallback(normalized_name or food_name, 100, 'g')
    except Exception as fallback_err:
        print(f"[WARN] Profile fallback lookup failed for '{normalized_name or food_name}': {fallback_err}")

    if fallback_nutrition and fallback_source:
        dp = default_profile or {
            'density': 0.95,
            'calories_pg': 1.2,
            'protein_pg': 0.04,
            'carbs_pg': 0.13,
            'fat_pg': 0.04,
        }
        return {
            'found': True,
            'food_name': str(fallback_nutrition.get('food_name', normalized_name or food_name or 'unknown food') or 'unknown food'),
            'food_id': 0,
            'density': dp['density'],
            'calories_pg': float(fallback_nutrition.get('calories', 0) or 0) / 100.0,
            'protein_pg': float(fallback_nutrition.get('protein', 0) or 0) / 100.0,
            'carbs_pg': float(fallback_nutrition.get('carbs', 0) or 0) / 100.0,
            'fat_pg': float(fallback_nutrition.get('fat', 0) or 0) / 100.0,
        }

    # Fallback profile for unknown names.
    dp = default_profile or {
        'density': 0.95,
        'calories_pg': 1.2,
        'protein_pg': 0.04,
        'carbs_pg': 0.13,
        'fat_pg': 0.04,
    }
    return {
        'found': False,
        'food_name': (normalized_name or food_name or 'unknown food').strip() or 'unknown food',
        'food_id': 0,
        'density': dp['density'],
        'calories_pg': dp['calories_pg'],
        'protein_pg': dp['protein_pg'],
        'carbs_pg': dp['carbs_pg'],
        'fat_pg': dp['fat_pg'],
    }


def _csv_float_value(row, *keys, default=0.0):
    for key in keys:
        raw = str((row or {}).get(key, '') or '').strip()
        if not raw:
            continue
        try:
            return float(raw)
        except Exception:
            continue
    return default


def _default_food_profile():
    ensure_nutrition_csv_fresh()
    if not csv_data:
        return {
            'density': 0.95,
            'calories_pg': 1.2,
            'protein_pg': 0.04,
            'carbs_pg': 0.13,
            'fat_pg': 0.04,
        }

    dense_vals = []
    cals = []
    proteins = []
    carbs = []
    fats = []
    for row in csv_data:
        cname = normalize_food_name(row.get('category_name', ''))
        if cname == 'background':
            continue
        d = _csv_float_value(row, 'density', default=0.0)
        if d > 0:
            dense_vals.append(d)
        cals.append(_csv_float_value(row, 'calories', default=0.0))
        proteins.append(_csv_float_value(row, 'protein', default=0.0))
        carbs.append(_csv_float_value(row, 'carbohydrates', default=0.0))
        fats.append(_csv_float_value(row, 'fat', default=0.0))

    return {
        'density': float(np.median(dense_vals)) if dense_vals else 0.95,
        'calories_pg': float(np.mean(cals)) if cals else 1.2,
        'protein_pg': float(np.mean(proteins)) if proteins else 0.04,
        'carbs_pg': float(np.mean(carbs)) if carbs else 0.13,
        'fat_pg': float(np.mean(fats)) if fats else 0.04,
    }


def _estimate_segment_thickness_cm(area_fraction, food_name=''):
    # Heuristic range for food pile height on a standard plate.
    base = 0.8 + 1.6 * np.sqrt(max(0.0, min(1.0, float(area_fraction))))
    lname = normalize_food_name(food_name)
    if any(k in lname for k in ['soup', 'sauce', 'yogurt', 'milk', 'juice']):
        base *= 0.75
    if any(k in lname for k in ['steak', 'chicken', 'tofu', 'salmon', 'meat']):
        base *= 1.15
    return float(np.clip(base, 0.5, 2.8))


def _infer_food_name_from_region(region_rgb):
    """Classify food name from a region image using Claude Vision AI."""
    
    # Fallback to heuristic if no Claude client
    if not claude_client or not ANTHROPIC_API_KEY:
        return _infer_food_name_from_region_heuristic(region_rgb)
    
    try:
        from PIL import Image
        import io
        
        # Convert numpy array to PIL Image
        if isinstance(region_rgb, np.ndarray):
            # Ensure we have a 2D or 3D array of reasonable size
            if region_rgb.ndim == 1:
                region_rgb = region_rgb.reshape(-1, 3)
            if region_rgb.shape[1] != 3:
                return _infer_food_name_from_region_heuristic(region_rgb)
            
            # Create a small representative image (100x100 max)
            h = min(100, max(10, len(region_rgb) // 10))
            w = min(100, max(10, len(region_rgb) // 10))
            
            # Tile or reshape the RGB data into an image
            region_array = np.tile(region_rgb.reshape(-1, 3), (h*w // len(region_rgb) + 1, 1))[:h*w]
            region_array = region_array.reshape(h, w, 3).astype(np.uint8)
            img = Image.fromarray(region_array, 'RGB')
        else:
            return _infer_food_name_from_region_heuristic(region_rgb)
        
        # Convert to base64
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        img_b64 = base64.standard_b64encode(img_byte_arr.read()).decode('utf-8')
        
        # Query Claude Vision
        message = claude_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=50,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "This is a food item on a plate. Identify the specific food in ONE or TWO words only. Be concise. Return only the food name, nothing else. Examples: 'broccoli', 'grilled chicken', 'rice', 'tomato salad'."
                        }
                    ],
                }
            ],
        )
        
        food_name = message.content[0].text.strip().lower()
        
        # Clean up the response - remove punctuation and take first item if multiple
        food_name = re.sub(r'[^\w\s]', '', food_name).strip()
        if ' ' in food_name:
            food_name = food_name.split()[0]
        
        if food_name and len(food_name) > 1:
            print(f"[Claude Vision] Classified food region as: {food_name}")
            return food_name
        
    except Exception as e:
        print(f"[Claude Vision] Error: {e}, falling back to heuristic")
    
    # Fallback to heuristic
    return _infer_food_name_from_region_heuristic(region_rgb)


def _infer_food_name_from_region_heuristic(region_rgb):
    """Heuristic vegetable inference from region color statistics (fallback)."""
    if region_rgb is None or len(region_rgb) == 0:
        return 'mixed vegetable salad'

    mean_rgb = np.mean(region_rgb.astype(np.float32), axis=0)
    r, g, b = float(mean_rgb[0]), float(mean_rgb[1]), float(mean_rgb[2])

    # Strong color priors for common salad ingredients.
    if r > g * 1.18 and r > b * 1.18:
        return 'tomato'
    if g > r * 1.15 and g > b * 1.10:
        if g < 95 or (r < 90 and b < 90):
            return 'broccoli'
        if b > 95 and r > 90:
            return 'cucumber'
        return 'lettuce'
    if r > 160 and g > 115 and b < 120:
        return 'carrot'
    if r > 170 and g > 170 and b < 145:
        return 'corn'
    if b > 120 and r > 100 and (b - g) > 12:
        return 'red cabbage'
    if r > 180 and g > 170 and b > 150:
        return 'onion'
    return 'mixed vegetable salad'


def _aggregate_food_items(items):
    """Merge repeated food names and sum their nutrition/weight."""
    grouped = {}
    for it in items:
        name = str(it.get('food_name', 'mixed vegetable salad') or 'mixed vegetable salad')
        if name not in grouped:
            grouped[name] = {
                'food_id': it.get('food_id', 0),
                'food_name': name,
                'volume_cm3': 0.0,
                'weight_g': 0.0,
                'calories': 0.0,
                'protein': 0.0,
                'fat': 0.0,
                'carbs': 0.0,
                'fiber': 0.0,
                'sugars': 0.0,
            }

        for key in ['volume_cm3', 'weight_g', 'calories', 'protein', 'fat', 'carbs', 'fiber', 'sugars']:
            grouped[name][key] += float(it.get(key, 0) or 0)

    out = []
    for _, v in grouped.items():
        out.append({
            'food_id': int(v['food_id']),
            'food_name': v['food_name'],
            'volume_cm3': round(v['volume_cm3'], 2),
            'weight_g': round(v['weight_g'], 2),
            'calories': round(v['calories'], 2),
            'protein': round(v['protein'], 2),
            'fat': round(v['fat'], 2),
            'carbs': round(v['carbs'], 2),
            'fiber': round(v['fiber'], 2),
            'sugars': round(v['sugars'], 2),
        })

    out.sort(key=lambda x: x.get('weight_g', 0), reverse=True)
    return out


def prepare_image_parts_for_sizing(image_path, food_hints_text='', plate_diameter_cm=DEFAULT_PLATE_DIAMETER_CM):
    """Prepare segmented food-part cutouts and metadata before nutrition estimation."""
    ensure_nutrition_csv_fresh()

    try:
        from PIL import Image
    except Exception as e:
        raise RuntimeError(f'Pillow is required for image analysis: {e}')

    try:
        img = Image.open(image_path).convert('RGB')
    except Exception as e:
        raise ValueError(
            'Could not decode this image format. Please upload JPG, PNG, WEBP, BMP, or TIFF. '
            'If this is HEIC/HEIF from iPhone, convert to JPG first.'
        ) from e

    rgb = np.array(img)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('Uploaded image must be RGB.')

    h, w = rgb.shape[:2]
    if h < 64 or w < 64:
        raise ValueError('Image is too small for analysis.')

    yy, xx = np.ogrid[:h, :w]
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    plate_radius_px = min(h, w) * 0.46
    plate_mask = ((xx - cx) ** 2 + (yy - cy) ** 2) <= (plate_radius_px ** 2)

    rgb_f = rgb.astype(np.float32)
    vmax = np.max(rgb_f, axis=2)
    vmin = np.min(rgb_f, axis=2)
    sat = np.where(vmax > 0, (vmax - vmin) / np.maximum(vmax, 1.0), 0.0) * 255.0

    plate_brightness = float(np.median(vmax[plate_mask]))
    food_mask = plate_mask & (vmax > 18) & (
        (sat > 24) |
        (vmax < (plate_brightness - 12))
    )
    food_mask = ndi.binary_opening(food_mask, structure=np.ones((3, 3), dtype=bool))
    food_mask = ndi.binary_closing(food_mask, structure=np.ones((5, 5), dtype=bool))

    hints = _split_food_hints(food_hints_text)

    labels, count = ndi.label(food_mask)
    plate_pixels = int(np.sum(plate_mask))
    min_pixels = max(250, int(plate_pixels * 0.01))
    components = []
    for label_id in range(1, count + 1):
        area_px = int(np.sum(labels == label_id))
        if area_px < min_pixels:
            continue
        components.append((label_id, area_px))
    components.sort(key=lambda x: x[1], reverse=True)

    if not components and np.any(food_mask):
        components = [(1, int(np.sum(food_mask)))]
        labels = np.where(food_mask, 1, 0).astype(np.int32)

    if len(components) == 1 and not hints:
        main_id = components[0][0]
        main_mask = labels == main_id
        r = rgb_f[:, :, 0]
        g = rgb_f[:, :, 1]
        b = rgb_f[:, :, 2]

        color_masks = [
            main_mask & (r > g * 1.12) & (r > b * 1.12),
            main_mask & (g > r * 1.10) & (g > b * 1.06),
            main_mask & (r > 165) & (g > 120) & (b < 130),
            main_mask & (r > 175) & (g > 165) & (b > 145),
        ]

        split_labels = np.zeros_like(labels, dtype=np.int32)
        split_components = []
        next_id = 1
        for cm in color_masks:
            if np.sum(cm) < min_pixels:
                continue
            cm_labels, cm_count = ndi.label(cm)
            for cid in range(1, cm_count + 1):
                seg = cm_labels == cid
                area_px = int(np.sum(seg))
                if area_px < min_pixels:
                    continue
                split_labels[seg] = next_id
                split_components.append((next_id, area_px))
                next_id += 1

        remain = main_mask & (split_labels == 0)
        if np.sum(remain) >= min_pixels:
            rm_labels, rm_count = ndi.label(remain)
            for rid in range(1, rm_count + 1):
                seg = rm_labels == rid
                area_px = int(np.sum(seg))
                if area_px < min_pixels:
                    continue
                split_labels[seg] = next_id
                split_components.append((next_id, area_px))
                next_id += 1

        if len(split_components) >= 2:
            labels = split_labels
            components = sorted(split_components, key=lambda x: x[1], reverse=True)

    components = components[:6]
    if not components:
        labels = np.where(plate_mask, 1, 0).astype(np.int32)
        components = [(1, int(np.sum(plate_mask)))]

    vis = rgb.copy()
    region_colors = [
        np.array([230, 92, 47], dtype=np.uint8),
        np.array([240, 168, 53], dtype=np.uint8),
        np.array([76, 175, 80], dtype=np.uint8),
        np.array([40, 145, 200], dtype=np.uint8),
        np.array([170, 95, 220], dtype=np.uint8),
        np.array([220, 70, 130], dtype=np.uint8),
    ]
    region_map = np.zeros((h, w), dtype=np.uint8)

    image_filename = os.path.basename(image_path)
    base_name = os.path.splitext(image_filename)[0]
    output_dir = os.path.join('static', 'foodseg', base_name)
    cutout_dir = os.path.join(output_dir, 'cutouts')
    os.makedirs(cutout_dir, exist_ok=True)

    parts = []
    for idx, (label_id, area_px) in enumerate(components):
        region_mask = labels == label_id
        if not np.any(region_mask):
            continue

        region_rgb = rgb[region_mask]
        hint_name = hints[idx] if idx < len(hints) else ''
        suggested_name = hint_name or _infer_food_name_from_region(region_rgb)

        ys, xs = np.where(region_mask)
        x_min, x_max = int(np.min(xs)), int(np.max(xs))
        y_min, y_max = int(np.min(ys)), int(np.max(ys))

        crop_rgb = rgb[y_min:y_max + 1, x_min:x_max + 1]
        crop_mask = region_mask[y_min:y_max + 1, x_min:x_max + 1]
        rgba = np.zeros((crop_rgb.shape[0], crop_rgb.shape[1], 4), dtype=np.uint8)
        rgba[:, :, :3] = crop_rgb
        rgba[:, :, 3] = np.where(crop_mask, 255, 0).astype(np.uint8)

        part_id = idx + 1
        cutout_name = f'part_{part_id}.png'
        cutout_disk = os.path.join(cutout_dir, cutout_name)
        Image.fromarray(rgba, mode='RGBA').save(cutout_disk)

        color = region_colors[idx % len(region_colors)]
        vis[region_mask] = (0.45 * vis[region_mask] + 0.55 * color).astype(np.uint8)
        region_map[region_mask] = part_id

        parts.append({
            'part_id': part_id,
            'suggested_food_name': suggested_name,
            'food_name': suggested_name,
            'bbox': {
                'x_min': x_min,
                'y_min': y_min,
                'x_max': x_max,
                'y_max': y_max,
            },
            'area_fraction': round(float(area_px) / float(max(plate_pixels, 1)), 4),
            'cutout_image': f'/static/foodseg/{base_name}/cutouts/{cutout_name}',
            'container_type': 'plate',
            'container_size_cm': round(float(plate_diameter_cm), 2),
            'container_depth_cm': 2.0,
            'fill_ratio': 0.6,
        })

    vis_path = os.path.join(output_dir, f'{base_name}_labeled_seg.png')
    Image.fromarray(vis).save(vis_path)

    region_map_path = os.path.join(output_dir, f'{base_name}_region_map.png')
    Image.fromarray(region_map, mode='L').save(region_map_path)

    payload = {
        'image_name': base_name,
        'image_filename': image_filename,
        'image_origin': f'/static/uploads/{image_filename}',
        'plate_diameter_cm': round(float(plate_diameter_cm), 2),
        'food_hints_text': str(food_hints_text or ''),
        'parts': parts,
        'visualization_path': vis_path,
        'region_map_path': region_map_path,
    }
    _save_parts_metadata(image_filename, payload)
    return payload


def analyze_uploaded_meal_image(image_path, food_hints_text='', plate_diameter_cm=DEFAULT_PLATE_DIAMETER_CM, part_contexts=None):
    """Estimate food masses and nutrition from a top-down meal image on a standard plate."""
    ensure_nutrition_csv_fresh()

    try:
        from PIL import Image
    except Exception as e:
        raise RuntimeError(f'Pillow is required for image analysis: {e}')

    try:
        img = Image.open(image_path).convert('RGB')
    except Exception as e:
        raise ValueError(
            'Could not decode this image format. Please upload JPG, PNG, WEBP, BMP, or TIFF. '
            'If this is HEIC/HEIF from iPhone, convert to JPG first.'
        ) from e

    # Normalize very large images to keep analysis stable and responsive.
    # Many phone photos are 3000-5000px wide and can slow or break segmentation.
    max_side = max(img.size)
    if max_side > 900:
        scale = 900.0 / float(max_side)
        new_size = (
            max(1, int(round(img.size[0] * scale))),
            max(1, int(round(img.size[1] * scale))),
        )
        try:
            resample = Image.Resampling.LANCZOS
        except Exception:
            resample = Image.LANCZOS
        img = img.resize(new_size, resample)

    rgb = np.array(img)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('Uploaded image must be RGB.')

    # Prefer Doubao vision for direct food + mass estimation; fallback to segmentation heuristic.
    try:
        doubao_result = _build_doubao_meal_analysis(
            image_path=image_path,
            rgb=rgb,
            food_hints_text=food_hints_text,
            part_contexts=part_contexts,
        )
        if doubao_result:
            return doubao_result
    except Exception as e:
        print(f"[WARN] Doubao image analysis failed, fallback to heuristic: {e}")

    h, w = rgb.shape[:2]
    if h < 64 or w < 64:
        raise ValueError('Image is too small for analysis.')

    yy, xx = np.ogrid[:h, :w]
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    plate_radius_px = min(h, w) * 0.46
    plate_mask = ((xx - cx) ** 2 + (yy - cy) ** 2) <= (plate_radius_px ** 2)

    rgb_f = rgb.astype(np.float32)
    vmax = np.max(rgb_f, axis=2)
    vmin = np.min(rgb_f, axis=2)
    sat = np.where(vmax > 0, (vmax - vmin) / np.maximum(vmax, 1.0), 0.0) * 255.0

    plate_brightness = float(np.median(vmax[plate_mask]))
    food_mask = plate_mask & (vmax > 18) & (
        (sat > 24) |
        (vmax < (plate_brightness - 12))
    )
    food_mask = ndi.binary_opening(food_mask, structure=np.ones((3, 3), dtype=bool))
    food_mask = ndi.binary_closing(food_mask, structure=np.ones((5, 5), dtype=bool))

    hints = _split_food_hints(food_hints_text)

    labels, count = ndi.label(food_mask)
    plate_pixels = int(np.sum(plate_mask))
    min_pixels = max(250, int(plate_pixels * 0.01))
    components = []
    for label_id in range(1, count + 1):
        area_px = int(np.sum(labels == label_id))
        if area_px < min_pixels:
            continue
        components.append((label_id, area_px))
    components.sort(key=lambda x: x[1], reverse=True)

    if not components and np.any(food_mask):
        components = [(1, int(np.sum(food_mask)))]
        labels = np.where(food_mask, 1, 0).astype(np.int32)

    # If salad ingredients are touching, split one big mask by color groups.
    if len(components) == 1 and not hints:
        main_id = components[0][0]
        main_mask = labels == main_id
        r = rgb_f[:, :, 0]
        g = rgb_f[:, :, 1]
        b = rgb_f[:, :, 2]

        color_masks = [
            main_mask & (r > g * 1.12) & (r > b * 1.12),
            main_mask & (g > r * 1.10) & (g > b * 1.06),
            main_mask & (r > 165) & (g > 120) & (b < 130),
            main_mask & (r > 175) & (g > 165) & (b > 145),
        ]

        split_labels = np.zeros_like(labels, dtype=np.int32)
        split_components = []
        next_id = 1
        for cm in color_masks:
            if np.sum(cm) < min_pixels:
                continue
            cm_labels, cm_count = ndi.label(cm)
            for cid in range(1, cm_count + 1):
                seg = cm_labels == cid
                area_px = int(np.sum(seg))
                if area_px < min_pixels:
                    continue
                split_labels[seg] = next_id
                split_components.append((next_id, area_px))
                next_id += 1

        # Add remaining area not covered by color masks.
        remain = main_mask & (split_labels == 0)
        if np.sum(remain) >= min_pixels:
            rm_labels, rm_count = ndi.label(remain)
            for rid in range(1, rm_count + 1):
                seg = rm_labels == rid
                area_px = int(np.sum(seg))
                if area_px < min_pixels:
                    continue
                split_labels[seg] = next_id
                split_components.append((next_id, area_px))
                next_id += 1

        if len(split_components) >= 2:
            labels = split_labels
            components = sorted(split_components, key=lambda x: x[1], reverse=True)

    # Keep at most 6 dominant regions to avoid noisy tiny fragments.
    components = components[:6]
    if not components:
        # Fallback: treat the whole inner plate region as one mixed food area.
        labels = np.where(plate_mask, 1, 0).astype(np.int32)
        fallback_area_px = int(np.sum(plate_mask))
        components = [(1, fallback_area_px)]

    default_profile = _default_food_profile()
    plate_area_cm2 = np.pi * (max(float(plate_diameter_cm), 10.0) / 2.0) ** 2
    cm2_per_px = plate_area_cm2 / max(plate_pixels, 1)

    vis = rgb.copy()
    region_colors = [
        np.array([230, 92, 47], dtype=np.uint8),
        np.array([240, 168, 53], dtype=np.uint8),
        np.array([76, 175, 80], dtype=np.uint8),
        np.array([40, 145, 200], dtype=np.uint8),
        np.array([170, 95, 220], dtype=np.uint8),
        np.array([220, 70, 130], dtype=np.uint8),
    ]

    items = []
    hover_items = []
    region_map = np.zeros((h, w), dtype=np.uint8)
    for idx, (label_id, area_px) in enumerate(components):
        region_mask = labels == label_id
        region_rgb = rgb[region_mask]
        hover_id = idx + 1
        hinted_name = hints[idx] if idx < len(hints) else ''
        csv_row = _csv_row_first_match(hinted_name) if hinted_name else None

        if csv_row:
            food_name = csv_row.get('category_name', hinted_name or f'food_{idx + 1}')
            food_id = int(_safe_float(csv_row.get('category_id', idx + 1), idx + 1))
            density = _csv_float_value(csv_row, 'density', default=default_profile['density'])
            calories_pg = _csv_float_value(csv_row, 'calories', default=default_profile['calories_pg'])
            protein_pg = _csv_float_value(csv_row, 'protein', default=default_profile['protein_pg'])
            carbs_pg = _csv_float_value(csv_row, 'carbohydrates', default=default_profile['carbs_pg'])
            fat_pg = _csv_float_value(csv_row, 'fat', default=default_profile['fat_pg'])
        else:
            inferred_name = hinted_name or _infer_food_name_from_region(region_rgb)
            inferred_csv = _csv_row_first_match(inferred_name)
            if inferred_csv:
                food_name = inferred_csv.get('category_name', inferred_name)
                food_id = int(_safe_float(inferred_csv.get('category_id', idx + 1), idx + 1))
                density = _csv_float_value(inferred_csv, 'density', default=default_profile['density'])
                calories_pg = _csv_float_value(inferred_csv, 'calories', default=default_profile['calories_pg'])
                protein_pg = _csv_float_value(inferred_csv, 'protein', default=default_profile['protein_pg'])
                carbs_pg = _csv_float_value(inferred_csv, 'carbohydrates', default=default_profile['carbs_pg'])
                fat_pg = _csv_float_value(inferred_csv, 'fat', default=default_profile['fat_pg'])
            else:
                food_name = inferred_name
                food_id = idx + 1
                density = default_profile['density']
                calories_pg = default_profile['calories_pg']
                protein_pg = default_profile['protein_pg']
                carbs_pg = default_profile['carbs_pg']
                fat_pg = default_profile['fat_pg']

        area_fraction = float(area_px) / float(max(plate_pixels, 1))
        area_cm2 = area_px * cm2_per_px
        thickness_cm = _estimate_segment_thickness_cm(area_fraction, food_name)
        volume_cm3 = area_cm2 * thickness_cm
        weight_g = volume_cm3 * max(density, 0.1)

        carbs = weight_g * carbs_pg
        protein = weight_g * protein_pg
        fat = weight_g * fat_pg
        calories = weight_g * calories_pg

        color = region_colors[idx % len(region_colors)]
        vis[region_mask] = (0.45 * vis[region_mask] + 0.55 * color).astype(np.uint8)
        region_map[region_mask] = hover_id

        item = {
            'food_id': int(food_id),
            'food_name': food_name,
            'volume_cm3': round(float(volume_cm3), 2),
            'weight_g': round(float(weight_g), 2),
            'calories': round(float(calories), 2),
            'protein': round(float(protein), 2),
            'fat': round(float(fat), 2),
            'carbs': round(float(carbs), 2),
            'fiber': 0.0,
            'sugars': 0.0,
        }
        items.append(item)

        ys, xs = np.where(region_mask)
        if len(xs) > 0 and len(ys) > 0:
            bbox = {
                'x_min': int(np.min(xs)),
                'y_min': int(np.min(ys)),
                'x_max': int(np.max(xs)),
                'y_max': int(np.max(ys)),
            }
        else:
            bbox = {'x_min': 0, 'y_min': 0, 'x_max': 0, 'y_max': 0}

        hover_items.append({
            'id': int(hover_id),
            'food_name': item['food_name'],
            'weight_g': item['weight_g'],
            'calories': item['calories'],
            'carbs': item['carbs'],
            'protein': item['protein'],
            'fat': item['fat'],
            'bbox': bbox,
        })

    items = _aggregate_food_items(items)

    totals = {
        'total_volume_cm3': round(float(sum(x['volume_cm3'] for x in items)), 2),
        'total_weight_g': round(float(sum(x['weight_g'] for x in items)), 2),
        'calories': round(float(sum(x['calories'] for x in items)), 2),
        'protein': round(float(sum(x['protein'] for x in items)), 2),
        'fat': round(float(sum(x['fat'] for x in items)), 2),
        'carbs': round(float(sum(x['carbs'] for x in items)), 2),
        'fiber': 0.0,
        'sugars': 0.0,
        'food_items': items,
    }

    base_name = os.path.splitext(os.path.basename(image_path))[0]
    output_dir = os.path.join('static', 'foodseg', base_name)
    os.makedirs(output_dir, exist_ok=True)

    vis_path = os.path.join(output_dir, f'{base_name}_labeled_seg.png')
    Image.fromarray(vis).save(vis_path)

    region_map_path = os.path.join(output_dir, f'{base_name}_region_map.png')
    Image.fromarray(region_map, mode='L').save(region_map_path)

    nutrition_json_path = os.path.join(output_dir, f'{base_name}_nutrition.json')
    with open(nutrition_json_path, 'w', encoding='utf-8') as f:
        json.dump(totals, f, ensure_ascii=True, indent=4)

    hover_json_path = os.path.join(output_dir, f'{base_name}_hover.json')
    with open(hover_json_path, 'w', encoding='utf-8') as f:
        json.dump({'hover_items': hover_items}, f, ensure_ascii=True, indent=4)

    return {
        'image_origin': f'/static/uploads/{os.path.basename(image_path)}',
        'output_dir': output_dir,
        'nutrition_path': nutrition_json_path,
        'visualization_path': vis_path,
        'region_map_path': region_map_path,
        'hover_json_path': hover_json_path,
        'nutrition': totals,
    }

def upload_to_gcs(bucket_name, source_file_name, destination_blob_name):
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(source_file_name)
        # Make the blob publicly readable
        blob.make_public()
        return True
    except DefaultCredentialsError:
        return False
    except Exception as e:
        print(f"Error uploading to GCS: {e}")
        return False

    # print(f"File {source_file_name} uploaded to {destination_blob_name}.")

# Load nutrition data from CSV file instead of USDA API
# CSV columns: category_id, category_name, Density (g/ml), Calories (kcal/g), 
#              Protein (g/g), Carbohydrates (g/g), Fat (g/g), Reference (FDC ID)
CSV_NUTRITION_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "FoodSAM", "food_full_data_revised.csv"))
csv_data = []
csv_loaded = False
csv_mtime = None

# Simple cache to reduce repeated lookups
_search_cache = {}
_nutrition_cache = {}

def load_nutrition_csv():
    """Load the nutrition data from CSV file into memory"""
    global csv_data, csv_loaded, csv_mtime, _search_cache, _nutrition_cache
    try:
        csv_path = CSV_NUTRITION_PATH
        if os.path.exists(csv_path):
            csv_data = []
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    csv_data.append(row)
            csv_loaded = True
            csv_mtime = os.path.getmtime(csv_path)
            # Invalidate caches so lookups use refreshed CSV content.
            _search_cache.clear()
            _nutrition_cache.clear()
            print(f"Loaded nutrition data from CSV: {len(csv_data)} food items")
            return True
        else:
            print(f"CSV file not found at {csv_path}")
            return False
    except Exception as e:
        print(f"Error loading CSV: {e}")
        return False


def ensure_nutrition_csv_fresh():
    """Reload CSV if the source file changed on disk."""
    global csv_mtime
    try:
        if not os.path.exists(CSV_NUTRITION_PATH):
            return
        current_mtime = os.path.getmtime(CSV_NUTRITION_PATH)
        if (not csv_loaded) or (csv_mtime is None) or (current_mtime > csv_mtime):
            load_nutrition_csv()
    except Exception as e:
        print(f"[WARN] Could not refresh CSV data: {e}")

WORD_NUMBER_MAP = {
    'a': 1,
    'an': 1,
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
    'half': 0.5,
    'dozen': 12,
}


def _singularize(word):
    """Convert a single English word to its approximate singular base form."""
    if len(word) <= 2:
        return word
    # ies → y  (berries→berry, fries→fry, cranberries→cranberry)
    if word.endswith('ies') and len(word) > 4:
        return word[:-3] + 'y'
    # Explicit sibilant-ending plurals: strip 'es'
    # sses→ss, shes→sh, ches→ch, xes→x, zes→z  (e.g., peaches→peach, boxes→box)
    if (word.endswith('sses') or word.endswith('shes') or
            word.endswith('ches') or word.endswith('xes') or word.endswith('zes')):
        return word[:-2]
    # oes → o  (tomatoes→tomato, potatoes→potato)
    if word.endswith('oes') and len(word) > 4:
        return word[:-2]
    # For other 'es' endings: if stripping just 's' leaves a word ending in 'e',
    # that 'e' was part of the base (noodles→noodle, olives→olive, grapes→grape)
    if word.endswith('es') and len(word) > 3:
        stem_s = word[:-1]   # strip just 's' → keeps trailing 'e'
        if stem_s.endswith('e'):
            return stem_s    # noodles→noodle ✓
        return word[:-2]     # fallback: strip 'es'
    # Plain 's' plural (beans→bean, shoots→shoot, dumplings→dumpling, peas→pea)
    if word.endswith('s') and len(word) > 2:
        return word[:-1]
    return word


def normalize_food_name(name):
    """Normalize food names to their singular base form for better CSV matching.

    Applies per-word singularization so that multi-word names work correctly,
    e.g. 'green beans' → 'green bean', 'wonton dumplings' → 'wonton dumpling',
    'noodles' → 'noodle', 'dried cranberries' → 'dried cranberry'.
    """
    cleaned = (name or '').strip().lower()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return ' '.join(_singularize(w) for w in cleaned.split())


def _cutouts_for_food(food_name, cutouts_by_food):
    """Match a nutrition food name to one or more recognized cutout image paths."""
    target = normalize_food_name(food_name)
    if not target:
        return []

    if target in cutouts_by_food:
        return cutouts_by_food[target]

    # Fallback: allow partial containment when model labels vary slightly.
    for key, images in cutouts_by_food.items():
        if not key:
            continue
        if key in target or target in key:
            return images

    return []


def _normalize_web_image_path(path):
    """Normalize stored image path into a browser-safe /static/... URL."""
    raw = str(path or '').strip()
    if not raw:
        return ''

    normalized = raw.replace('\\', '/').strip()
    if normalized.startswith('http://') or normalized.startswith('https://'):
        return normalized
    if normalized.startswith('/static/'):
        return normalized

    marker = '/static/'
    idx = normalized.lower().find(marker)
    if idx >= 0:
        return normalized[idx:]

    if normalized.startswith('static/'):
        return '/' + normalized

    return normalized


def parse_direct_macro_input(text):
    """Parse direct nutrition input like '100g carb', '-20 fat', '1000kcal', or '-100 kcal'."""
    cleaned = (text or '').strip()

    macro_kcal_per_g = {
        'carbs': 4.0,
        'protein': 4.0,
        'fat': 9.0,
    }

    macro_match = re.match(
        r'^([+-]?\d+(?:\.\d+)?)\s*g?\s*(carb|carbon|carbohydrate|protein|fat)s?$',
        cleaned,
        re.IGNORECASE,
    )
    calorie_match = re.match(
        r'^([+-]?\d+(?:\.\d+)?)\s*(kcal|cal|calorie|calories)$',
        cleaned,
        re.IGNORECASE,
    )

    if not macro_match and not calorie_match:
        return None

    nutrition = {'carbs': 0.0, 'protein': 0.0, 'fat': 0.0, 'calories': 0.0}

    if macro_match:
        amount = float(macro_match.group(1))
        macro_type = macro_match.group(2).lower()
        if macro_type in ['carb', 'carbon', 'carbohydrate']:
            nutrition['carbs'] = amount
            macro_label = 'carbs'
        elif macro_type == 'protein':
            nutrition['protein'] = amount
            macro_label = 'protein'
        else:
            nutrition['fat'] = amount
            macro_label = 'fat'
        nutrition['calories'] = amount * macro_kcal_per_g[macro_label]
        return {
            'food_name': f"direct {macro_label}",
            'quantity': amount,
            'unit': 'g',
            'carbs': round(nutrition['carbs'], 2),
            'protein': round(nutrition['protein'], 2),
            'fat': round(nutrition['fat'], 2),
            'calories': round(nutrition['calories'], 2),
        }

    amount = float(calorie_match.group(1))
    nutrition['calories'] = amount
    return {
        'food_name': 'direct calories',
        'quantity': amount,
        'unit': 'kcal',
        'carbs': 0.0,
        'protein': 0.0,
        'fat': 0.0,
        'calories': round(nutrition['calories'], 2),
    }

def parse_food_input(food_input):
    """
    Parse user input like "100g chicken breast", "1 medium apple", or "two eggs".
    Returns: (food_name, quantity, unit)
    """
    cleaned = food_input.strip()

    # Define valid measurement units
    valid_units = {
        'g', 'gram', 'grams', 'kg', 'kilogram', 'kilograms',
        'oz', 'ounce', 'ounces', 'lb', 'lbs', 'pound', 'pounds',
        'ml', 'milliliter', 'milliliters', 'l', 'liter', 'liters',
        'cup', 'cups', 'tbsp', 'tablespoon', 'tablespoons',
        'tsp', 'teaspoon', 'teaspoons', 'piece', 'pieces',
        'serving', 'servings', 'slice', 'slices'
    }

    # Pattern: number + optional unit + food name (e.g., "100g chicken breast")
    match = re.match(r'^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?\s+(.+)$', cleaned)
    if match:
        quantity = float(match.group(1))
        raw_unit = (match.group(2) or '').lower()
        remaining_text = match.group(3).strip()

        # Check if raw_unit is a valid measurement unit
        if raw_unit and raw_unit in valid_units:
            # It's a valid unit, so remaining_text is the food name
            food_name = remaining_text
            unit = raw_unit
        elif raw_unit:
            # Not a valid unit - treat it as part of food name
            food_name = f"{raw_unit} {remaining_text}"
            # Determine unit based on food type
            countable_keywords = ['egg', 'eggs', 'apple', 'apples', 'banana', 'bananas', 'orange', 'oranges', 'bread', 'breads', 'hamburger', 'hamburgers', 'burger', 'burgers', 'sandwich', 'sandwiches']
            unit = 'unit' if any(k in food_name.lower() for k in countable_keywords) else 'g'
        else:
            # No unit detected, determine based on food type
            food_name = remaining_text
            countable_keywords = ['egg', 'eggs', 'apple', 'apples', 'banana', 'bananas', 'orange', 'oranges', 'bread', 'breads', 'hamburger', 'hamburgers', 'burger', 'burgers', 'sandwich', 'sandwiches']
            unit = 'unit' if any(k in food_name.lower() for k in countable_keywords) else 'g'

        return food_name, quantity, unit

    # Pattern: word-number + food name (e.g., "two eggs", "a banana")
    word_match = re.match(r'^(?P<num_word>[a-zA-Z]+)\s+(?P<food>.+)$', cleaned.lower())
    if word_match:
        num_word = word_match.group('num_word')
        food_name = word_match.group('food').strip()
        if num_word in WORD_NUMBER_MAP:
            return food_name, float(WORD_NUMBER_MAP[num_word]), 'unit'

    # Fallback: treat as a single unit
    return cleaned, 1.0, 'unit'

def search_csv_food(food_name):
    """
    Search for food in the loaded CSV data.
    Returns a dictionary matching the expected format.
    Uses improved matching: exact > prefix > word boundary > no substring fallback.
    """
    ensure_nutrition_csv_fresh()

    if not csv_loaded or not csv_data:
        print("CSV data not loaded")
        return None
    
    normalized_query = normalize_food_name(food_name)
    cache_key = normalized_query
    if cache_key in _search_cache:
        print(f"[CACHE HIT] Using cached results for '{food_name}'")
        return _search_cache[cache_key]

    exact_matches = []
    prefix_matches = []
    word_boundary_matches = []
    
    for row in csv_data:
        category_name = row.get('category_name', '')
        normalized_category = normalize_food_name(category_name)
        if not normalized_category or normalized_category == 'background':
            continue
        
        if normalized_query == normalized_category:
            # Exact match (highest priority)
            exact_matches.append(row)
        elif normalized_query and normalized_category.startswith(normalized_query):
            # Prefix match (e.g., "apple" matches "apple pie") - but only if it's a word boundary
            # Check that the next character after query is a space or end of string
            next_pos = len(normalized_query)
            if next_pos >= len(normalized_category) or normalized_category[next_pos] == ' ':
                prefix_matches.append(row)
        elif normalized_query and normalized_query in normalized_category.split():
            # Word boundary match (e.g., "apple" is a complete word in "green apple")
            word_boundary_matches.append(row)

    # Use the best match category available
    matching_foods = exact_matches if exact_matches else (
        prefix_matches if prefix_matches else word_boundary_matches
    )
    
    if not matching_foods:
        print(f"No foods found for '{food_name}' in CSV data")
        return None
    
    # Convert CSV rows to match USDA API response format
    foods = []
    for food_row in matching_foods:
        foods.append({
            'fdcId': str(food_row.get('Reference (FDC ID)', food_row.get('category_id', ''))),
            'description': food_row.get('category_name', ''),
            'dataType': 'CSV',
            'foodCategory': {'description': food_row.get('category_name', '')},
            # Store our own data for later retrieval
            '_csv_data': food_row
        })
    
    response = {'foods': foods}
    
    # Cache the result
    _search_cache[cache_key] = response
    print(f"[CACHED] Stored results for '{food_name}' - Found {len(foods)} items")
    
    return response

def get_food_nutrition_csv(fdc_id, csv_food_row, quantity, unit):
    """
    Get nutrition info from CSV data.
    csv_food_row: The CSV row dictionary containing nutrition data
    quantity: amount user consumed
    unit: unit of measurement (g, cup, etc.)
    """
    print(f"\nCSV Nutrition Data for {csv_food_row.get('category_name', 'Unknown')}")
    
    food_name = csv_food_row.get('category_name', '')
    
    # Extract nutrition values from CSV (values are per gram).
    # The CSV uses short lowercase headers: calories, protein, carbohydrates, fat, density.
    def _csv_float(row, *keys, default=0.0):
        """Try each key in order and return the first non-empty float value found."""
        for key in keys:
            raw = str(row.get(key, '') or '').strip()
            if raw:
                try:
                    return float(raw)
                except ValueError:
                    continue
        return default

    try:
        calories_per_gram = _csv_float(
            csv_food_row,
            'calories', 'Calories', 'Calories (kcal/g)', 'calories (kcal/g)',
        )
        protein_per_gram = _csv_float(
            csv_food_row,
            'protein', 'Protein', 'Protein (g/g)', 'protein (g/g)',
        )
        carbs_per_gram = _csv_float(
            csv_food_row,
            'carbohydrates', 'Carbohydrates', 'carbs', 'Carbs',
            'Carbohydrates (g/g)', 'carbohydrates (g/g)',
        )
        fat_per_gram = _csv_float(
            csv_food_row,
            'fat', 'Fat', 'Fat (g/g)', 'fat (g/g)',
        )
        density = _csv_float(
            csv_food_row,
            'density', 'Density', 'Density (g/ml)', 'density (g/ml)',
            default=1.0,
        )
        if density == 0.0:
            density = 1.0
    except Exception as e:
        print(f"Error parsing nutrition values: {e}")
        return None
    
    print(f"Food: {food_name}")
    print(f"Nutrition per gram: Calories={calories_per_gram}, Protein={protein_per_gram}g, Carbs={carbs_per_gram}g, Fat={fat_per_gram}g")
    
    # Convert quantity to grams
    quantity_in_grams = quantity
    unit_lower = unit.lower()
    food_name_lower = food_name.lower()
    
    # Descriptive sizes with USDA-style defaults
    if unit_lower in ['small', 'sm']:
        if 'egg' in food_name_lower:
            quantity_in_grams = quantity * 50
        elif 'apple' in food_name_lower:
            quantity_in_grams = quantity * 149
        elif 'banana' in food_name_lower:
            quantity_in_grams = quantity * 101
        else:
            quantity_in_grams = quantity * 100
    
    elif unit_lower in ['medium', 'med', 'md']:
        if 'egg' in food_name_lower:
            quantity_in_grams = quantity * 60
        elif 'apple' in food_name_lower:
            quantity_in_grams = quantity * 182
        elif 'banana' in food_name_lower:
            quantity_in_grams = quantity * 118
        elif 'orange' in food_name_lower:
            quantity_in_grams = quantity * 131
        else:
            quantity_in_grams = quantity * 150
    
    elif unit_lower in ['large', 'lg', 'big']:
        if 'egg' in food_name_lower:
            quantity_in_grams = quantity * 70
        elif 'apple' in food_name_lower:
            quantity_in_grams = quantity * 223
        elif 'banana' in food_name_lower:
            quantity_in_grams = quantity * 136
        else:
            quantity_in_grams = quantity * 200
    
    # Volume units (using density if available)
    elif unit_lower in ['cup', 'cups']:
        quantity_in_grams = quantity * 240 * density
    elif unit_lower in ['tbsp', 'tablespoon', 'tablespoons']:
        quantity_in_grams = quantity * 15 * density
    elif unit_lower in ['tsp', 'teaspoon', 'teaspoons']:
        quantity_in_grams = quantity * 5 * density
    
    # Weight units
    elif unit_lower in ['oz', 'ounce', 'ounces']:
        quantity_in_grams = quantity * 28.35
    elif unit_lower in ['lb', 'lbs', 'pound', 'pounds']:
        quantity_in_grams = quantity * 453.59
    elif unit_lower in ['g', 'gram', 'grams']:
        quantity_in_grams = quantity
    
    # Liquid volume (ml)
    elif unit_lower in ['ml', 'milliliter', 'milliliters']:
        quantity_in_grams = quantity * density
    
    # Countable items
    elif unit_lower in ['piece', 'pieces', 'item', 'items', 'unit', 'units', 'egg', 'eggs', 'slice', 'slices', 'toast', 'toasts']:
        default_piece_weight = 150
        if 'egg' in food_name_lower:
            default_piece_weight = 60
        elif 'banana' in food_name_lower:
            default_piece_weight = 118
        elif 'apple' in food_name_lower:
            default_piece_weight = 182
        elif 'orange' in food_name_lower:
            default_piece_weight = 131
        elif 'bread' in food_name_lower:
            default_piece_weight = 30  # 1 slice of bread ≈ 30g
        quantity_in_grams = quantity * default_piece_weight
    
    else:
        # Unknown unit - assume grams
        print(f"[WARNING] Unknown unit '{unit}' - treating quantity as grams")
        quantity_in_grams = quantity
    
    print(f"Input: {quantity}{unit} = {quantity_in_grams:.2f}g")
    
    # Calculate based on per-gram values
    nutrition = {
        'carbs': round(carbs_per_gram * quantity_in_grams, 2),
        'protein': round(protein_per_gram * quantity_in_grams, 2),
        'fat': round(fat_per_gram * quantity_in_grams, 2),
        'calories': round(calories_per_gram * quantity_in_grams, 2)
    }
    
    print(f"Final nutrition: {nutrition}")
    print("=" * 50 + "\n")
    
    return {
        'food_name': food_name,
        'carbs': nutrition['carbs'],
        'protein': nutrition['protein'],
        'fat': nutrition['fat'],
        'calories': nutrition['calories'],
        'quantity': quantity,
        'unit': unit,
        'serving_size': 1  # CSV data is per gram, so serving is 1g
    }

# USDA API fallback functions
# Prefer USDA_API_KEY; fallback to DATA_GOV_API_KEY; final fallback to DEMO_KEY.
USDA_API_KEY = os.getenv("USDA_API_KEY") or os.getenv("DATA_GOV_API_KEY", "DEMO_KEY")
USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"

# Doubao LLM API configuration for nutrition queries
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY") or os.getenv("ARK_API_KEY", "")
DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
DOUBAO_MODEL = "doubao-1-5-pro-32k-250115"
DOUBAO_IMAGE_MODEL = os.getenv("DOUBAO_IMAGE_MODEL", "doubao-seed-2-0-mini-260215")


def _extract_json_object_from_text(text):
    """Extract first JSON object from model text output."""
    cleaned = str(text or '').strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    m = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _query_doubao_image_food_mass(image_path, food_hints_text='', part_contexts=None):
    """Use Doubao vision model to detect foods and estimate mass in grams from one meal image."""
    if not DOUBAO_API_KEY:
        return None

    try:
        with open(image_path, 'rb') as f:
            image_bytes = f.read()
        image_b64 = base64.b64encode(image_bytes).decode('utf-8')
    except Exception as e:
        print(f"[Doubao Vision] Failed to read image: {e}")
        return None

    hints = [x.strip() for x in re.split(r'[,，;]+', str(food_hints_text or '')) if x.strip()]
    hint_text = ''
    if hints:
        hint_text = 'Possible foods from user hints: ' + ', '.join(hints) + '. Use hints only when visually plausible.'
    part_context_text = _build_part_context_text(part_contexts)

    prompt = (
        "You are a nutrition image analyst. Analyze this meal image and estimate foods and their mass. "
        "Return ONLY valid JSON object with schema: "
        "{\"foods\":[{\"food_name\":\"string\",\"mass_g\":number,\"confidence\":number}],\"notes\":\"string\"}. "
        "Rules: 1) 1-8 foods max. 2) food_name concise in English, singular when possible. "
        "3) mass_g in grams, positive number, realistic meal portions. 4) confidence between 0 and 1. "
        "5) Prioritize user-provided per-part container sizing context when estimating masses. "
        "6) food_name must be only the food itself (no cooking style/adjectives/region words), e.g. 'rice', 'beef', 'radish', 'cucumber'. "
        "7) Do not output markdown/code fences/extra text. "
        f"{hint_text} {part_context_text}"
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DOUBAO_API_KEY}",
    }

    def _parse_foods_from_model_text(content):
        parsed = _extract_json_object_from_text(content)
        if not isinstance(parsed, dict):
            print(f"[Doubao Vision] Could not parse JSON content: {content}")
            return None

        foods = parsed.get('foods', [])
        if not isinstance(foods, list) or not foods:
            print(f"[Doubao Vision] Empty foods result: {parsed}")
            return None

        clean = []
        for item in foods[:8]:
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get('food_name', '') or '').strip()
            name = _simplify_food_name(raw_name)
            if not name:
                continue
            try:
                mass = float(item.get('mass_g', 0) or 0)
            except Exception:
                mass = 0.0
            try:
                conf = float(item.get('confidence', 0.6) or 0.6)
            except Exception:
                conf = 0.6
            if mass <= 0:
                continue
            mass = float(np.clip(mass, 5.0, 1200.0))
            conf = float(np.clip(conf, 0.0, 1.0))
            clean.append({
                'food_name': name,
                'mass_g': mass,
                'confidence': conf,
            })

        return clean or None

    try:
        # Try Chat Completions style first.
        payload_chat = {
            "model": DOUBAO_IMAGE_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 600,
        }
        resp = requests.post(DOUBAO_BASE_URL, json=payload_chat, headers=headers, timeout=40)
        if resp.ok:
            data = resp.json()
            content = (
                (data.get('choices') or [{}])[0]
                .get('message', {})
                .get('content', '')
            )
            parsed = _parse_foods_from_model_text(content)
            if parsed:
                print(f"[Doubao Vision] Parsed foods via chat/completions: {parsed}")
                return parsed
        else:
            print(f"[Doubao Vision] chat/completions failed: {resp.status_code} {resp.text[:300]}")

        # Fallback: try Responses API format used in latest Ark docs.
        responses_url = "https://ark.cn-beijing.volces.com/api/v3/responses"
        payload_responses = {
            "model": DOUBAO_IMAGE_MODEL,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{image_b64}",
                        },
                        {
                            "type": "input_text",
                            "text": prompt,
                        },
                    ],
                }
            ],
            "max_output_tokens": 600,
        }
        resp2 = requests.post(responses_url, json=payload_responses, headers=headers, timeout=40)
        resp2.raise_for_status()
        data2 = resp2.json()
        content2 = data2.get('output_text', '')
        if not content2:
            out = data2.get('output', []) or []
            chunks = []
            for item in out:
                for c in (item.get('content', []) or []):
                    txt = c.get('text')
                    if txt:
                        chunks.append(str(txt))
            content2 = '\n'.join(chunks)

        parsed2 = _parse_foods_from_model_text(content2)
        if parsed2:
            print(f"[Doubao Vision] Parsed foods via responses API: {parsed2}")
            return parsed2
        return None
    except Exception as e:
        print(f"[Doubao Vision] Request failed: {e}")
        return None


def _build_doubao_meal_analysis(image_path, rgb, food_hints_text='', part_contexts=None):
    """Build nutrition report from Doubao food+mass predictions."""
    rows = _query_doubao_image_food_mass(
        image_path,
        food_hints_text=food_hints_text,
        part_contexts=part_contexts,
    )
    if not rows:
        return None

    default_profile = _default_food_profile()
    raw_items = []
    hover_items = []
    h, w = rgb.shape[:2]

    part_lookup = {}
    for p in (part_contexts or []):
        try:
            pid = int(_safe_float((p or {}).get('part_id', 0), 0))
            if pid > 0:
                part_lookup[pid] = p
        except Exception:
            continue

    for idx, row in enumerate(rows):
        food_name = row['food_name']
        weight_g = float(row['mass_g'])

        profile = _food_profile_from_name(food_name, default_profile=default_profile)
        resolved_name = profile.get('food_name', food_name)
        density = max(float(profile.get('density', default_profile['density']) or default_profile['density']), 0.1)

        calories = round(weight_g * float(profile['calories_pg']), 2)
        carbs = round(weight_g * float(profile['carbs_pg']), 2)
        protein = round(weight_g * float(profile['protein_pg']), 2)
        fat = round(weight_g * float(profile['fat_pg']), 2)
        volume_cm3 = round(weight_g / density, 2)

        item = {
            'food_id': int(profile.get('food_id', idx + 1) or (idx + 1)),
            'food_name': resolved_name,
            'volume_cm3': round(volume_cm3, 2),
            'weight_g': round(weight_g, 2),
            'calories': calories,
            'protein': protein,
            'fat': fat,
            'carbs': carbs,
            'fiber': 0.0,
            'sugars': 0.0,
        }
        raw_items.append(item)

        # Without grounding coordinates, use full-image bbox placeholders for hover metadata.
        part_id = int(idx + 1)
        part_meta = part_lookup.get(part_id, {})
        hover_items.append({
            'id': part_id,
            'food_name': item['food_name'],
            'weight_g': item['weight_g'],
            'calories': item['calories'],
            'carbs': item['carbs'],
            'protein': item['protein'],
            'fat': item['fat'],
            'cutout_image': str(part_meta.get('cutout_image', '') or ''),
            'bbox': {
                'x_min': 0,
                'y_min': 0,
                'x_max': int(max(w - 1, 0)),
                'y_max': int(max(h - 1, 0)),
            },
            'confidence': round(float(row.get('confidence', 0.6)), 3),
        })

    items = _aggregate_food_items(raw_items)
    totals = {
        'total_volume_cm3': round(float(sum(x['volume_cm3'] for x in items)), 2),
        'total_weight_g': round(float(sum(x['weight_g'] for x in items)), 2),
        'calories': round(float(sum(x['calories'] for x in items)), 2),
        'protein': round(float(sum(x['protein'] for x in items)), 2),
        'fat': round(float(sum(x['fat'] for x in items)), 2),
        'carbs': round(float(sum(x['carbs'] for x in items)), 2),
        'fiber': 0.0,
        'sugars': 0.0,
        'food_items': items,
        'analysis_source': DOUBAO_IMAGE_MODEL,
    }

    from PIL import Image

    base_name = os.path.splitext(os.path.basename(image_path))[0]
    output_dir = os.path.join('static', 'foodseg', base_name)
    os.makedirs(output_dir, exist_ok=True)

    vis_path = os.path.join(output_dir, f'{base_name}_labeled_seg.png')
    Image.fromarray(rgb).save(vis_path)

    region_map = np.zeros((h, w), dtype=np.uint8)
    region_map_path = os.path.join(output_dir, f'{base_name}_region_map.png')
    Image.fromarray(region_map, mode='L').save(region_map_path)

    nutrition_json_path = os.path.join(output_dir, f'{base_name}_nutrition.json')
    with open(nutrition_json_path, 'w', encoding='utf-8') as f:
        json.dump(totals, f, ensure_ascii=True, indent=4)

    hover_json_path = os.path.join(output_dir, f'{base_name}_hover.json')
    with open(hover_json_path, 'w', encoding='utf-8') as f:
        json.dump({'hover_items': hover_items}, f, ensure_ascii=True, indent=4)

    return {
        'image_origin': f'/static/uploads/{os.path.basename(image_path)}',
        'output_dir': output_dir,
        'nutrition_path': nutrition_json_path,
        'visualization_path': vis_path,
        'region_map_path': region_map_path,
        'hover_json_path': hover_json_path,
        'nutrition': totals,
    }

def query_doubao_nutrition(food_name, quantity, unit):
    """
    Query Doubao LLM API to get nutrition data for a food.
    Returns nutrition dict or None.
    """
    try:
        prompt = (
            f"Estimate the nutrition for {quantity}{unit} {food_name}. "
            "Return ONLY valid JSON with numeric values using this exact schema: "
            '{"calories": 0, "carbs": 0, "protein": 0, "fat": 0}. '
            "Calories must be in kcal. Carbs, protein, and fat must be in grams. "
            "Do not include explanations, markdown, code fences, or any extra text."
        )
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DOUBAO_API_KEY}"
        }
        
        payload = {
            "model": DOUBAO_MODEL,
            "messages": [
                {"role": "system", "content": "你是一个营养学家助手，专门提供食物的营养信息。你必须只返回JSON，字段必须包含calories、carbs、protein、fat，全部为数字。"},
                {"role": "user", "content": prompt}
            ]
        }
        
        print(f"[Doubao] Querying nutrition for: {prompt}")
        response = requests.post(DOUBAO_BASE_URL, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        
        # Extract the assistant's response
        if 'choices' in data and len(data['choices']) > 0:
            message = data['choices'][0].get('message', {})
            content = message.get('content', '').strip()
            print(f"[Doubao] Response: {content}")
            
            nutrition = {
                'carbs': 0.0,
                'protein': 0.0,
                'fat': 0.0,
                'calories': 0.0
            }

            def _coerce_number(value):
                if value is None:
                    return 0.0
                if isinstance(value, (int, float)):
                    return float(value)
                text = str(value).strip()
                match = re.search(r'([\d.]+)', text)
                if match:
                    try:
                        return float(match.group(1))
                    except Exception:
                        return 0.0
                return 0.0

            def _extract_number(patterns, text):
                for pattern in patterns:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        try:
                            return float(match.group(1))
                        except Exception:
                            continue
                return 0.0

            # Prefer strict JSON if Doubao follows the instruction.
            cleaned = content.strip()
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\s*```$', '', cleaned)
            json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(0))
                    nutrition['calories'] = _coerce_number(
                        parsed.get('calories', parsed.get('calorie', parsed.get('energy', parsed.get('kcal', parsed.get('热量', parsed.get('能量', 0))))))
                    )
                    nutrition['carbs'] = _coerce_number(
                        parsed.get('carbs', parsed.get('carbohydrates', parsed.get('carbohydrate', parsed.get('碳水', parsed.get('碳水化合物', 0)))))
                    )
                    nutrition['protein'] = _coerce_number(
                        parsed.get('protein', parsed.get('proteins', parsed.get('蛋白质', 0)))
                    )
                    nutrition['fat'] = _coerce_number(
                        parsed.get('fat', parsed.get('fats', parsed.get('脂肪', 0)))
                    )
                except Exception:
                    pass

            # Fallback to flexible text parsing for English/Chinese/energy wording.
            if nutrition['calories'] <= 0:
                nutrition['calories'] = _extract_number([
                    r'calories?[^\d]{0,12}([\d.]+)',
                    r'energy[^\d]{0,12}([\d.]+)',
                    r'(?:热量|能量)[^\d]{0,12}([\d.]+)',
                    r'([\d.]+)\s*kcal',
                ], cleaned)
            if nutrition['carbs'] <= 0:
                nutrition['carbs'] = _extract_number([
                    r'carbs?[^\d]{0,12}([\d.]+)',
                    r'carbohydrates?[^\d]{0,12}([\d.]+)',
                    r'(?:碳水|碳水化合物)[^\d]{0,12}([\d.]+)',
                ], cleaned)
            if nutrition['protein'] <= 0:
                nutrition['protein'] = _extract_number([
                    r'protein[^\d]{0,12}([\d.]+)',
                    r'蛋白质[^\d]{0,12}([\d.]+)',
                ], cleaned)
            if nutrition['fat'] <= 0:
                nutrition['fat'] = _extract_number([
                    r'fat[^\d]{0,12}([\d.]+)',
                    r'脂肪[^\d]{0,12}([\d.]+)',
                ], cleaned)

            if nutrition['calories'] <= 0 and _is_nutrition_meaningful(nutrition):
                nutrition['calories'] = nutrition['carbs'] * 4 + nutrition['protein'] * 4 + nutrition['fat'] * 9
            
            if _is_nutrition_meaningful({'carbs': nutrition['carbs'], 'protein': nutrition['protein'], 'fat': nutrition['fat']}):
                result = {
                    'food_name': food_name,
                    'carbs': round(nutrition['carbs'], 2),
                    'protein': round(nutrition['protein'], 2),
                    'fat': round(nutrition['fat'], 2),
                    'calories': round(nutrition['calories'], 2),
                    'quantity': quantity,
                    'unit': unit,
                    'source': 'Doubao LLM'
                }
                print(f"[Doubao] Extracted nutrition: {result}")
                return result
            else:
                print(f"[Doubao] Response parsed but nutrition not meaningful: {nutrition}")
                return None
        else:
            print(f"[Doubao] No choices in response")
            return None
            
    except Exception as e:
        print(f"[Doubao] Error querying nutrition: {e}")
        return None

def search_usda_food(food_name):
    """
    Search for food in USDA FoodData Central via API.
    Returns a dictionary with search results or None if not found.
    """
    try:
        print(f"\nSearching USDA API for: {food_name}")
        url = f"{USDA_BASE_URL}/foods/search"
        merged = []
        seen_ids = set()

        query_candidates = [food_name]
        normalized = normalize_food_name(food_name)
        if normalized and normalized != food_name:
            query_candidates.append(normalized)

        # Two-pass strategy: broad searches with increasing pageSize
        # USDA API doesn't support dataType filtering in search params
        for q in query_candidates:
            for pass_num in range(2):
                page_size = 25 if pass_num == 0 else 50
                params = {
                    'query': q,
                    'pageSize': page_size,
                    'api_key': USDA_API_KEY
                }
                try:
                    response = requests.get(url, params=params, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    foods = data.get('foods', [])
                    for item in foods:
                        fdc_id = str(item.get('fdcId', ''))
                        if not fdc_id or fdc_id in seen_ids:
                            continue
                        seen_ids.add(fdc_id)
                        merged.append(item)
                except Exception as pass_err:
                    print(f"[USDA] Pass {pass_num + 1} error for '{q}': {pass_err}")
                    continue

        if merged:
            print(f"[USDA] Found {len(merged)} merged results for '{food_name}'")
            return {'foods': merged}

        print(f"[USDA] No results found for '{food_name}'")
        return None
    except Exception as e:
        print(f"[USDA] Error searching for '{food_name}': {e}")
        return None

def _is_nutrition_meaningful(nutrition):
    if not nutrition:
        return False
    return any(float(nutrition.get(k, 0) or 0) > 0 for k in ['carbs', 'protein', 'fat', 'calories'])

def _extract_usda_nutrition_per_100g(nutrients):
    """Extract macros from USDA nutrients across different payload shapes."""
    nutrition_per_100g = {
        'calories': 0.0,
        'protein': 0.0,
        'carbs': 0.0,
        'fat': 0.0
    }

    # USDA nutrientNumber reference in FoodData Central:
    # 208=Energy (kcal), 203=Protein, 205=Carbohydrate, 204=Total lipid (fat)
    for nutrient in nutrients or []:
        nutrient_obj = nutrient.get('nutrient', {}) if isinstance(nutrient, dict) else {}

        nutrient_name = str(
            nutrient_obj.get('name')
            or nutrient.get('nutrientName')
            or ''
        ).strip().lower()

        nutrient_number = str(
            nutrient_obj.get('number')
            or nutrient.get('nutrientNumber')
            or ''
        ).strip()

        nutrient_id = str(
            nutrient_obj.get('id')
            or nutrient.get('nutrientId')
            or ''
        ).strip()

        unit_name = str(
            nutrient_obj.get('unitName')
            or nutrient.get('unitName')
            or ''
        ).strip().lower()

        amount = nutrient.get('amount')
        if amount is None:
            amount = nutrient.get('value', 0)

        try:
            amount = float(amount or 0)
        except Exception:
            amount = 0.0

        if nutrient_number == '208' or nutrient_id == '1008' or ('energy' in nutrient_name and unit_name == 'kcal'):
            nutrition_per_100g['calories'] = amount
        elif nutrient_number == '203' or nutrient_id == '1003' or 'protein' in nutrient_name:
            nutrition_per_100g['protein'] = amount
        elif nutrient_number == '205' or nutrient_id == '1005' or ('carbohydrate' in nutrient_name and 'fiber' not in nutrient_name):
            nutrition_per_100g['carbs'] = amount
        elif nutrient_number == '204' or nutrient_id == '1004' or ('fat' in nutrient_name and ('total' in nutrient_name or 'lipid' in nutrient_name)):
            nutrition_per_100g['fat'] = amount

    return nutrition_per_100g

def get_food_nutrition_usda(fdc_id, quantity, unit):
    """
    Get nutrition info from USDA API.
    fdc_id: FDC ID from USDA food search
    quantity: amount user consumed
    unit: unit of measurement (g, cup, etc.)
    """
    try:
        print(f"\nFetching USDA nutrition data for FDC ID: {fdc_id}")
        url = f"{USDA_BASE_URL}/food/{fdc_id}"
        params = {'api_key': USDA_API_KEY}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        food_data = response.json()
        food_name = food_data.get('description', 'Unknown Food')
        nutrients = food_data.get('foodNutrients', [])
        
        # Extract key nutrients (per 100g serving from USDA)
        nutrition_per_100g = _extract_usda_nutrition_per_100g(nutrients)
        
        # Convert quantity to grams (similar logic as CSV)
        quantity_in_grams = quantity
        unit_lower = unit.lower()
        food_name_lower = food_name.lower()
        
        # Descriptive sizes with USDA-style defaults
        if unit_lower in ['small', 'sm']:
            quantity_in_grams = quantity * 100
        elif unit_lower in ['medium', 'med', 'md']:
            quantity_in_grams = quantity * 150
        elif unit_lower in ['large', 'lg', 'big']:
            quantity_in_grams = quantity * 200
        elif unit_lower in ['cup', 'cups']:
            quantity_in_grams = quantity * 240
        elif unit_lower in ['tbsp', 'tablespoon', 'tablespoons']:
            quantity_in_grams = quantity * 15
        elif unit_lower in ['tsp', 'teaspoon', 'teaspoons']:
            quantity_in_grams = quantity * 5
        elif unit_lower in ['oz', 'ounce', 'ounces']:
            quantity_in_grams = quantity * 28.35
        elif unit_lower in ['lb', 'lbs', 'pound', 'pounds']:
            quantity_in_grams = quantity * 453.59
        elif unit_lower in ['ml', 'milliliter', 'milliliters']:
            quantity_in_grams = quantity
        elif unit_lower in ['g', 'gram', 'grams']:
            quantity_in_grams = quantity
        elif unit_lower in ['piece', 'pieces', 'item', 'items', 'unit', 'units', 'egg', 'eggs', 'slice', 'slices', 'toast', 'toasts']:
            default_piece_weight = 150
            quantity_in_grams = quantity * default_piece_weight
        else:
            quantity_in_grams = quantity
        
        # Calculate nutrition (USDA provides per 100g, so scale accordingly)
        multiplier = quantity_in_grams / 100.0
        
        nutrition = {
            'carbs': round(nutrition_per_100g['carbs'] * multiplier, 2),
            'protein': round(nutrition_per_100g['protein'] * multiplier, 2),
            'fat': round(nutrition_per_100g['fat'] * multiplier, 2),
            'calories': round(nutrition_per_100g['calories'] * multiplier, 2)
        }
        
        print(f"USDA Nutrition: {nutrition} (from {quantity}{unit} = {quantity_in_grams:.2f}g)")
        
        result = {
            'food_name': food_name,
            'carbs': nutrition['carbs'],
            'protein': nutrition['protein'],
            'fat': nutrition['fat'],
            'calories': nutrition['calories'],
            'quantity': quantity,
            'unit': unit,
            'source': 'USDA API'
        }
        if not _is_nutrition_meaningful(result):
            print(f"[USDA] Nutrition data incomplete for FDC ID {fdc_id}, skipping this candidate")
            return None

        return result
    except Exception as e:
        print(f"[USDA] Error fetching nutrition: {e}")
        return None

MISSING_FOODS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "missing_foods.txt")

def _log_missing_food(food_name, quantity, unit, source, nutrition):
    """Append a food not found in CSV to missing_foods.txt."""
    try:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        carbs = round(float(nutrition.get('carbs', 0)), 2) if nutrition else 'N/A'
        protein = round(float(nutrition.get('protein', 0)), 2) if nutrition else 'N/A'
        fat = round(float(nutrition.get('fat', 0)), 2) if nutrition else 'N/A'
        calories = round(float(nutrition.get('calories', 0)), 2) if nutrition else 'N/A'
        line = (
            f"[{timestamp}] food={food_name!r} | qty={quantity}{unit} | "
            f"source={source} | carbs={carbs}g protein={protein}g fat={fat}g calories={calories}kcal\n"
        )
        with open(MISSING_FOODS_LOG, 'a', encoding='utf-8') as f:
            f.write(line)
        print(f"[MissingFoodLog] Logged: {food_name!r} (source: {source})")
    except Exception as e:
        print(f"[MissingFoodLog] Failed to write log: {e}")


def get_food_nutrition_with_fallback(food_name, quantity, unit):
    """
    Resolve nutrition for a food item using a three-tier fallback:
      1. Local CSV database
      2. USDA FoodData Central API
      3. Doubao LLM
    Foods not found in CSV are recorded in missing_foods.txt.
    Returns tuple: (nutrition_dict_or_none, source_label)
    """
    # --- 1. CSV ---
    csv_results = search_csv_food(food_name)
    if csv_results and csv_results.get('foods'):
        for food_item in csv_results['foods']:
            csv_row = food_item.get('_csv_data')
            if not csv_row:
                continue
            fdc_id = food_item.get('fdcId', '')
            nutrition = get_food_nutrition_csv(fdc_id, csv_row, quantity, unit)
            if nutrition and _is_nutrition_meaningful(nutrition):
                print(f"[Fallback] Found in CSV for '{food_name}'")
                return nutrition, "CSV"

    # Not in CSV — will log regardless of where it's eventually resolved.

    # --- 2. USDA API ---
    usda_results = search_usda_food(food_name)
    if usda_results and usda_results.get('foods'):
        for food_item in usda_results['foods'][:5]:
            fdc_id = food_item.get('fdcId')
            if not fdc_id:
                continue
            nutrition = get_food_nutrition_usda(fdc_id, quantity, unit)
            if nutrition and _is_nutrition_meaningful(nutrition):
                print(f"[Fallback] Found via USDA API for '{food_name}'")
                _log_missing_food(food_name, quantity, unit, "USDA API", nutrition)
                return nutrition, "USDA API"

    # --- 3. Doubao LLM ---
    nutrition = query_doubao_nutrition(food_name, quantity, unit)
    if nutrition and _is_nutrition_meaningful(nutrition):
        print(f"[Fallback] Found via Doubao LLM for '{food_name}'")
        _log_missing_food(food_name, quantity, unit, "Doubao LLM", nutrition)
        return nutrition, "Doubao LLM"

    # Not found anywhere — still log it
    _log_missing_food(food_name, quantity, unit, "NOT FOUND", None)
    return None, ""


DIET_SCALE = [
    (0.50 / 4.1, 0.20 / 4.1, 0.30 / 8.8),
    (0.60 / 4.1, 0.20 / 4.1, 0.20 / 8.8),
    (0.20 / 4.1, 0.30 / 4.1, 0.50 / 8.8),
    (0.28 / 4.1, 0.39 / 4.1, 0.33 / 8.8),
]


def calculate_macro_targets(gender, age, height, weight, carbohydrate, protein, fat, activity, diet):
    rmr = calculate_rmr(weight, height, age, gender)
    calories = calculate_daily_calories(rmr, activity)
    carbohydrate_intake, protein_intake, fat_intake = (calories * i for i in DIET_SCALE[diet])
    carbohydrate_needed = carbohydrate_intake - carbohydrate
    protein_needed = protein_intake - protein
    fat_needed = fat_intake - fat
    return {
        'calories': round(calories, 2),
        'carbohydrate_intake': round(carbohydrate_intake, 2),
        'protein_intake': round(protein_intake, 2),
        'fat_intake': round(fat_intake, 2),
        'carbohydrate_needed': round(carbohydrate_needed, 2),
        'protein_needed': round(protein_needed, 2),
        'fat_needed': round(fat_needed, 2),
        'need_vector': np.array([carbohydrate_needed, protein_needed, fat_needed], dtype=float),
    }


def _looks_non_veg_name(food_name_text):
    n = (food_name_text or '').lower()
    tags = ['chicken', 'beef', 'pork', 'fish', 'shrimp', 'mutton', 'lamb', 'meat', 'tuna', 'salmon']
    return any(t in n for t in tags)


def _looks_snack_or_dessert_name(food_name_text):
    n = (food_name_text or '').lower()
    blocked = ['candy', 'chocolate', 'cake', 'cookie', 'soda', 'syrup', 'chips', 'popcorn', 'cracker', 'biscuit', 'snack']
    return any(t in n for t in blocked)


def resolve_recipe_food_candidates(food_names, preference):
    resolved = []
    unresolved = []
    seen = set()

    for raw_name in food_names:
        cleaned = (raw_name or '').strip()
        if not cleaned:
            continue
        key = normalize_food_name(cleaned)
        if key in seen:
            continue
        seen.add(key)

        nutrition, source = get_food_nutrition_with_fallback(cleaned, 100, 'g')
        if not nutrition or not _is_nutrition_meaningful(nutrition):
            unresolved.append(cleaned)
            continue

        resolved_name = nutrition.get('food_name') or cleaned
        if preference and _looks_non_veg_name(resolved_name):
            unresolved.append(cleaned)
            continue

        vec = np.array([
            float(nutrition.get('carbs', 0) or 0) / 100.0,
            float(nutrition.get('protein', 0) or 0) / 100.0,
            float(nutrition.get('fat', 0) or 0) / 100.0,
        ], dtype=float)
        if np.sum(vec) <= 1e-8:
            unresolved.append(cleaned)
            continue

        resolved.append({
            'name': resolved_name,
            'input_name': cleaned,
            'source': source,
            'vec': vec,
        })

    return resolved, unresolved


def suggest_foods_for_deficit(deficit_vec, excluded_names=None, preference=0, limit=6):
    ensure_nutrition_csv_fresh()

    excluded = {normalize_food_name(x) for x in (excluded_names or [])}
    suggestions = []

    def row_float(row, *keys, default=0.0):
        for key in keys:
            raw = str(row.get(key, '') or '').strip()
            if not raw:
                continue
            try:
                return float(raw)
            except Exception:
                continue
        return default

    for row in (csv_data or []):
        fname = (row.get('category_name') or '').strip()
        if not fname:
            continue
        norm = normalize_food_name(fname)
        if norm in excluded or norm == 'background':
            continue
        if preference and _looks_non_veg_name(fname):
            continue
        if _looks_snack_or_dessert_name(fname):
            continue

        vec = np.array([
            row_float(row, 'carbohydrates', 'Carbohydrates', 'carbs', 'Carbs', default=0.0),
            row_float(row, 'protein', 'Protein', default=0.0),
            row_float(row, 'fat', 'Fat', default=0.0),
        ], dtype=float)
        if np.sum(vec) <= 1e-8:
            continue

        score = float(np.dot(vec * 100.0, np.maximum(deficit_vec, 0.0)))
        if score > 0:
            suggestions.append((score, fname))

    suggestions.sort(key=lambda x: x[0], reverse=True)
    result = []
    seen = set()
    for _, fname in suggestions:
        norm = normalize_food_name(fname)
        if norm in seen:
            continue
        seen.add(norm)
        result.append(fname)
        if len(result) >= limit:
            break
    return result


def build_recipe_option(title, candidates, target_need, use_all_requested=False):
    if not candidates:
        return None

    positive_target = np.maximum(target_need, 0.0)
    nutr = np.array([c['vec'] for c in candidates], dtype=float)
    count = len(candidates)
    min_grams = np.array([15.0 if use_all_requested else 0.0] * count, dtype=float)
    max_grams = np.array([350.0] * count, dtype=float)
    x0 = np.array([max(60.0, min_grams[i]) for i in range(count)], dtype=float)

    def objective(extra_amounts):
        grams = min_grams + np.maximum(extra_amounts, 0.0)
        supplied = np.dot(grams, nutr)
        under = np.maximum(positive_target - supplied, 0.0)
        over = np.maximum(supplied - positive_target, 0.0)
        return float(np.sum(under ** 2) + 4.0 * np.sum(over ** 2) + 0.0008 * np.sum(grams))

    try:
        res = minimize(
            objective,
            np.maximum(x0 - min_grams, 0.0),
            method='L-BFGS-B',
            bounds=[(0.0, max_grams[i] - min_grams[i]) for i in range(count)]
        )
        extra = res.x if res.success else np.maximum(x0 - min_grams, 0.0)
    except Exception:
        extra = np.maximum(x0 - min_grams, 0.0)

    grams = np.clip(min_grams + np.maximum(extra, 0.0), min_grams, max_grams)
    supplied = np.dot(grams, nutr)
    under = np.maximum(positive_target - supplied, 0.0)
    over = np.maximum(supplied - positive_target, 0.0)

    foods = []
    for idx, candidate in enumerate(candidates):
        gram = round(float(grams[idx]), 2)
        if gram <= 0:
            continue
        foods.append({
            'name': candidate['name'],
            'gram': gram,
            'source': candidate.get('source', ''),
        })

    if not foods:
        return None

    return {
        'title': title,
        'uses_all_requested': use_all_requested,
        'foods': foods,
        'supplied': {
            'carbs': round(float(supplied[0]), 2),
            'protein': round(float(supplied[1]), 2),
            'fat': round(float(supplied[2]), 2),
        },
        'shortfall_total': round(float(np.sum(under)), 2),
        'exceed_total': round(float(np.sum(over)), 2),
        'score': round(float(np.sum(under) + 2.5 * np.sum(over)), 3),
    }


def build_custom_recipe_recommendations(food_text, target_need, preference, limit=4):
    requested_foods = [item.strip() for item in re.split(r'[,，;]+', str(food_text or '')) if item.strip()]
    requested_foods = requested_foods[:6]
    resolved, unresolved = resolve_recipe_food_candidates(requested_foods, preference)

    recipes = []
    if resolved:
        recipe1 = build_recipe_option('Recipe 1', resolved, target_need, use_all_requested=True)
        if recipe1:
            recipes.append(recipe1)

        subset_candidates = []
        for subset_size in range(1, len(resolved)):
            for idx_tuple in combinations(range(len(resolved)), subset_size):
                chosen = [resolved[i] for i in idx_tuple]
                recipe = build_recipe_option('', chosen, target_need, use_all_requested=False)
                if recipe:
                    subset_candidates.append(recipe)

        subset_candidates.sort(key=lambda r: (r['score'], r['exceed_total'], r['shortfall_total']))
        seen = set()
        for recipe in subset_candidates:
            key = tuple(sorted(f['name'] for f in recipe['foods']))
            if key in seen:
                continue
            seen.add(key)
            recipe['title'] = f"Recipe {len(recipes) + 1}"
            recipes.append(recipe)
            if len(recipes) >= limit:
                break

    advice = {'message': '', 'suggested_foods': [], 'unresolved_foods': unresolved}
    if unresolved:
        advice['message'] = 'Some requested foods could not be resolved from CSV/USDA/Doubao.'

    if recipes:
        deficit = np.maximum(target_need - np.array([
            float(recipes[0]['supplied']['carbs']),
            float(recipes[0]['supplied']['protein']),
            float(recipes[0]['supplied']['fat']),
        ]), 0.0)
        if np.sum(deficit) > 12.0 or unresolved:
            advice['suggested_foods'] = suggest_foods_for_deficit(deficit, [f['name'] for f in resolved], preference, limit=6)
            if advice['suggested_foods'] and not advice['message']:
                advice['message'] = 'Your requested foods alone may not fully meet the supplementary nutrition.'
    elif requested_foods:
        advice['message'] = 'Could not build recipes from the requested foods.'
        advice['suggested_foods'] = suggest_foods_for_deficit(target_need, [], preference, limit=6)

    return {
        'requested_foods': requested_foods,
        'resolved_foods': [r['name'] for r in resolved],
        'unresolved_foods': unresolved,
        'recipes': recipes,
        'advice': advice,
    }

# Load CSV data on app startup
with app.app_context():
    load_nutrition_csv()

@app.route('/')
def main():
    # Redirect base URL to chatbot page
    return redirect('/chatbot')


@app.route('/upload_image', methods=["GET", "POST"])
def upload_image():
    if request.method == 'GET':
        return render_template('upload-image.html')

    file = request.files.get('upload-image')
    if not file or not file.filename:
        return render_template('upload-image.html', error='Please choose an image file.'), 400

    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        ext = _uploaded_image_extension(file, filename)
    if not ext:
        return render_template('upload-image.html', error='Supported image types: JPG, JPEG, PNG, WEBP, JFIF, BMP, TIFF.'), 400

    saved_name = f"meal_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}"
    upload_dir = os.path.abspath(app.config['UPLOAD_FOLDER'])
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, saved_name)
    file.save(save_path)

    food_hints = request.form.get('food-hints', '')
    plate_diameter_cm = _safe_float(request.form.get('plate-diameter-cm', DEFAULT_PLATE_DIAMETER_CM), DEFAULT_PLATE_DIAMETER_CM)

    try:
        prep = prepare_image_parts_for_sizing(
            image_path=save_path,
            food_hints_text=food_hints,
            plate_diameter_cm=plate_diameter_cm,
        )
    except Exception as e:
        print(f'[ERROR] upload_image pre-analysis failed: {e}')
        return render_template('upload-image.html', error=f'Image analysis failed: {e}'), 500

    return render_template('image-part-sizing.html', prep=prep)


@app.route('/analyze_image_parts', methods=['POST'])
def analyze_image_parts():
    """Finalize image nutrition after user confirms per-part container sizes."""
    image_filename = os.path.basename(str(request.form.get('image_filename', '') or '').strip())
    if not image_filename:
        return render_template('upload-image.html', error='Missing image info. Please upload again.'), 400

    metadata = _load_parts_metadata(image_filename)
    if not metadata:
        return render_template('upload-image.html', error='Part metadata missing. Please upload again.'), 400

    image_path = os.path.join(os.path.abspath(app.config['UPLOAD_FOLDER']), image_filename)
    if not os.path.exists(image_path):
        return render_template('upload-image.html', error='Uploaded image file was not found. Please upload again.'), 404

    part_ids = request.form.getlist('part_id')
    food_names = request.form.getlist('food_name')
    container_types = request.form.getlist('container_type')
    container_sizes = request.form.getlist('container_size_cm')
    container_depths = request.form.getlist('container_depth_cm')
    fill_ratios = request.form.getlist('fill_ratio')

    meta_part_map = {}
    for item in (metadata.get('parts') or []):
        try:
            pid = int(_safe_float((item or {}).get('part_id', 0), 0))
            if pid > 0:
                meta_part_map[pid] = item
        except Exception:
            continue

    part_contexts = []
    for idx, raw_id in enumerate(part_ids):
        part_id = int(_safe_float(raw_id, 0))
        if part_id <= 0:
            continue

        meta_part = meta_part_map.get(part_id, {})
        food_name = str(food_names[idx] if idx < len(food_names) else '').strip() or str(meta_part.get('food_name', '') or '')
        part_contexts.append({
            'part_id': part_id,
            'food_name': food_name or f'food_part_{part_id}',
            'container_type': str(container_types[idx] if idx < len(container_types) else 'plate').strip() or 'plate',
            'container_size_cm': _safe_float(container_sizes[idx] if idx < len(container_sizes) else '', metadata.get('plate_diameter_cm', DEFAULT_PLATE_DIAMETER_CM)),
            'container_depth_cm': _safe_float(container_depths[idx] if idx < len(container_depths) else '', 2.0),
            'fill_ratio': _safe_float(fill_ratios[idx] if idx < len(fill_ratios) else '', 0.6),
            'cutout_image': str(meta_part.get('cutout_image', '') or ''),
        })

    part_contexts.sort(key=lambda x: x.get('part_id', 0))
    food_hints_text = ', '.join([p['food_name'] for p in part_contexts if str(p.get('food_name', '')).strip()])

    try:
        result = analyze_uploaded_meal_image(
            image_path=image_path,
            food_hints_text=food_hints_text,
            plate_diameter_cm=_safe_float(metadata.get('plate_diameter_cm', DEFAULT_PLATE_DIAMETER_CM), DEFAULT_PLATE_DIAMETER_CM),
            part_contexts=part_contexts,
        )
    except Exception as e:
        print(f'[ERROR] analyze_image_parts failed: {e}')
        prep_fallback = metadata
        prep_fallback['error'] = f'Nutrition analysis failed: {e}'
        return render_template('image-part-sizing.html', prep=prep_fallback), 500

    return redirect(url_for('nutrition_calculation', path=result['image_origin']))

@app.route('/nutrition_calculation', methods=["GET", "POST"])
def nutrition_calculation():
    path = request.args.get('path', '')
    if not path:
        return redirect(url_for('upload_image'))

    image_filename = os.path.basename(path)
    name = os.path.splitext(image_filename)[0]
    nutrition_path = os.path.join('.', 'static', 'foodseg', name, f'{name}_nutrition.json')
    if not os.path.exists(nutrition_path):
        return render_template('upload-image.html', error='Nutrition output was not found for this image. Please upload again.'), 404

    with open(nutrition_path, 'r', encoding='utf-8') as f:
        nutrition_data = json.load(f)

    part_cutouts = []
    parts_meta = _load_parts_metadata(image_filename)
    if isinstance(parts_meta, dict):
        part_cutouts = parts_meta.get('parts', []) or []
    if not part_cutouts:
        try:
            upload_disk = os.path.join(os.path.abspath(app.config['UPLOAD_FOLDER']), image_filename)
            if os.path.exists(upload_disk):
                prepare_image_parts_for_sizing(
                    image_path=upload_disk,
                    food_hints_text='',
                    plate_diameter_cm=DEFAULT_PLATE_DIAMETER_CM,
                )
                parts_meta = _load_parts_metadata(image_filename)
                if isinstance(parts_meta, dict):
                    part_cutouts = parts_meta.get('parts', []) or []
        except Exception as cutout_err:
            print(f"[WARN] Failed to backfill cutout previews: {cutout_err}")

    for p in part_cutouts:
        if isinstance(p, dict):
            p['cutout_image'] = _normalize_web_image_path(p.get('cutout_image', ''))

    hover_items = []
    hover_path = os.path.join('.', 'static', 'foodseg', name, f'{name}_hover.json')
    if os.path.exists(hover_path):
        try:
            with open(hover_path, 'r', encoding='utf-8') as f:
                hover_data = json.load(f)
            hover_items = hover_data.get('hover_items', []) or []
            cutout_by_id = {}
            for p in part_cutouts:
                pid = int(_safe_float((p or {}).get('part_id', 0), 0))
                if pid > 0:
                    cutout_by_id[pid] = str((p or {}).get('cutout_image', '') or '')
            for item in hover_items:
                pid = int(_safe_float((item or {}).get('id', 0), 0))
                if pid in cutout_by_id and not item.get('cutout_image'):
                    item['cutout_image'] = cutout_by_id[pid]
                item['cutout_image'] = _normalize_web_image_path(item.get('cutout_image', ''))
        except Exception as e:
            print(f"[WARN] Failed to load hover metadata: {e}")

    cutouts_by_food = {}
    for item in hover_items:
        food_key = normalize_food_name(str((item or {}).get('food_name', '') or ''))
        cutout_img = _normalize_web_image_path((item or {}).get('cutout_image', ''))
        if not food_key or not cutout_img:
            continue
        cutouts_by_food.setdefault(food_key, [])
        if cutout_img not in cutouts_by_food[food_key]:
            cutouts_by_food[food_key].append(cutout_img)

    fallback_cutouts = []
    for p in part_cutouts:
        img = _normalize_web_image_path((p or {}).get('cutout_image', ''))
        if img and img not in fallback_cutouts:
            fallback_cutouts.append(img)

    for idx, food_item in enumerate((nutrition_data.get('food_items') or [])):
        fname = str((food_item or {}).get('food_name', '') or '')
        matched = _cutouts_for_food(fname, cutouts_by_food)
        if not matched and fallback_cutouts:
            matched = [fallback_cutouts[idx % len(fallback_cutouts)]]
        food_item['recognized_parts'] = matched

    region_map_web = './static/foodseg/' + name + '/' + name + "_region_map.png"
    region_map_disk = os.path.join('.', 'static', 'foodseg', name, f'{name}_region_map.png')
    ensure_nutrition_csv_fresh()
    food_name_options = []
    for row in (csv_data or []):
        cname = str(row.get('category_name', '') or '').strip()
        if not cname or normalize_food_name(cname) == 'background':
            continue
        food_name_options.append(cname)
    food_name_options = sorted(set(food_name_options), key=lambda x: x.lower())

    results = {
        'image_name': name,
        'image_origin': path,
        'image_seglab': './static/foodseg/' + name + '/' + name + "_labeled_seg.png",
        'image_region_map': region_map_web if os.path.exists(region_map_disk) else '',
        'part_cutouts': part_cutouts,
        'hover_items': hover_items,
        'food_name_options': food_name_options,
        'image_report': nutrition_data
    }

    # nutrition = {
    #     'carbohydrate': round(nutrition_data['carbs'], 2),
    #     'protein': round(nutrition_data['protein'], 2),
    #     'fat': round(nutrition_data['fat'], 2)
    # }

    if request.method == "POST":
        next = request.form["next"]
        if next: return redirect(url_for("data_collection", carbs=round(nutrition_data['carbs'], 2), protein=round(nutrition_data['protein'], 2), fat=round(nutrition_data['fat'], 2)))

    return render_template("nutrition-calculation.html", results=results)

def calculate_rmr(weight, height, age, sex):
    if sex == 0:
        rmr = (9.99 * weight) + (6.25 * height) - (4.92 * age) + 5 
    else:
        rmr = (9.99 * weight) + (6.25 * height) - (4.92 * age) - 161
    return rmr

def calculate_daily_calories(rmr, activity_level):
    if activity_level == 0:
        calories = rmr * 1.2
    elif activity_level == 1:
        calories = rmr * 1.375
    elif activity_level == 2:
        calories = rmr * 1.55
    else:
        calories = rmr * 1.725
    return calories

def constraint_func(x): 
    return x[0] + x[1]

# x, y [5, 10], z in [1, 3]
def calculate_cube_dimension(volume):
    x = y = 10
    z = 3

    for i in np.arange(5.0, 10.0, 1.0):
        if volume / (i * i) <= 3 and volume / (i * i) >= 1:
            z = volume / (i * i)
            x = y = i

    return x * 10.0, y * 10.0, z * 10.0

min_size = np.array([8.0, 8.0, 0.15])  # Minimum dimensions in cm
max_size = np.array([15.0, 13.0, 2.2])  # Maximum dimensions in cm
MAX_VOLUME = max_size[0] * max_size[1] * max_size[2] # Dimention is cm
TOLERANCE = 400  # allow feasible solutions even with moderate error

def calculate_cube_dimension(volume):
    # Define size limits in cm

    # Calculate minimum and maximum volume based on maximum dimensions
    min_volume = min_size[0] * min_size[1] * min_size[2]
    max_volume = max_size[0] * max_size[1] * max_size[2]

    # Check if the volume is valid
    if volume < min_volume: return min_size[0] * 10.0, min_size[1] * 10.0,  min_size[2] * 10.0
    if volume > max_volume: return max_size[0] * 10.0, max_size[1] * 10.0,  max_size[2] * 10.0  # Return zero if volume is invalid

    # Iterate through possible dimensions
    for x in np.arange(min_size[0], max_size[0] + 0.1, 0.1):  # Increment by 0.5 cm
        for y in np.arange(min_size[1], max_size[1] + 0.1, 0.1):  # Increment by 0.5 cm
            z = volume / (x * y)  # Calculate height based on volume
            # Check if height is within limits
            if min_size[2] <= z <= max_size[2]:
                return x * 10.0, y * 10.0, z * 10.0  # Return dimensions in mm

    return 0, 0, 0 # Return zero if no valid dimensions are found

def mesh_generation(name, weight, density, z_offset=0.0): #g/cm3, z_offset in mm
    x, y, z = calculate_cube_dimension(weight / density) # in mm
    # print(name, weight, density, weight / density, x, y, z)
    if (x == 0 or y == 0 or z == 0): return 0, 0, 0
    # print(x, y, z)
    # If mesh generation is disabled, just return dimensions without creating STL
    if MESH_MODE == 'none':
        return x, y, z

    # Lazy import to avoid loading numpy-stl unless needed
    try:
        from stl import mesh as stl_mesh
    except Exception as e:
        print(f"[WARN] Failed to import numpy-stl: {e}. Skipping STL generation.")
        return x, y, z

    # Center the box on the XY plane so all items share the same vertical axis.
    # z_offset shifts this item up to sit on top of the previous item.
    hx, hy = x / 2.0, y / 2.0
    z0, z1 = z_offset, z_offset + z
    vertices = np.array([
        [-hx, -hy, z0],
        [ hx, -hy, z0],
        [ hx,  hy, z0],
        [-hx,  hy, z0],
        [-hx, -hy, z1],
        [ hx, -hy, z1],
        [ hx,  hy, z1],
        [-hx,  hy, z1]])

    faces = np.array([[
        0,3,1],
        [1,3,2],
        [0,4,7],
        [0,7,3],
        [4,5,6],
        [4,6,7],
        [5,1,2],
        [5,2,6],
        [2,3,6],
        [3,7,6],
        [0,1,5],
        [0,5,4]])

    try:
        cube = stl_mesh.Mesh(np.zeros(faces.shape[0], dtype=stl_mesh.Mesh.dtype))
        for i, f in enumerate(faces):
            for j in range(3):
                cube.vectors[i][j] = vertices[f[j],:]

        import tempfile
        temp_dir = tempfile.gettempdir()
        tmp_path = os.path.join(temp_dir, name)
        blob_path = f"meshes/{name}"
        cube.save(tmp_path)

        if MESH_STORAGE == 'gcs':
            upload_to_gcs(bucket_name, tmp_path, blob_path)
            # Remove local temp file after upload
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        else:
            # Keep local file for direct download via /download-stl
            print(f"[INFO] Stored STL locally at {tmp_path}")
            # Record manifest to allow on-demand regeneration
            manifest = _load_manifest()
            manifest[name] = { 'amount': float(weight), 'density': float(density) }
            _save_manifest(manifest)
    except Exception as e:
        print(f"[WARN] STL generation/upload failed for {name}: {e}")

    return x, y, z


def _stl_to_triangles(stl_path):
    """Read a binary STL and return list of (v0,v1,v2) triangle tuples (mm)."""
    triangles = []
    try:
        with open(stl_path, 'rb') as f:
            f.read(80)  # header
            count = struct.unpack('<I', f.read(4))[0]
            for _ in range(count):
                f.read(12)  # normal
                v0 = struct.unpack('<fff', f.read(12))
                v1 = struct.unpack('<fff', f.read(12))
                v2 = struct.unpack('<fff', f.read(12))
                f.read(2)  # attr
                triangles.append((v0, v1, v2))
    except Exception as e:
        print(f'[WARN] _stl_to_triangles failed for {stl_path}: {e}')
    return triangles


def create_obj_bundle(stl_paths_and_names, output_obj_path):
    """
    Bundle multiple STL files into a single Wavefront OBJ file.
    stl_paths_and_names: list of (stl_file_path, object_name) tuples.
    Each STL becomes a named 'o' group; vertex indices are global and
    1-based as required by the OBJ spec.
    OBJ is plain text, requires no packaging, and is universally
    supported by all major slicers (PrusaSlicer, Cura, Bambu Studio,
    Blender, etc.).
    """
    lines = ['# ElevateFoods AI Nutrition Chatbot – multi-part food mesh\n']
    vertex_offset = 0
    obj_count = 0

    for stl_path, obj_name in stl_paths_and_names:
        triangles = _stl_to_triangles(stl_path)
        if not triangles:
            continue

        # Deduplicate vertices, round to 4 dp
        vert_map = {}
        verts = []
        tri_indices = []
        for v0, v1, v2 in triangles:
            idxs = []
            for v in (v0, v1, v2):
                key = (round(v[0], 4), round(v[1], 4), round(v[2], 4))
                if key not in vert_map:
                    vert_map[key] = len(verts)
                    verts.append(key)
                idxs.append(vert_map[key])
            tri_indices.append(idxs)

        # OBJ object name: replace whitespace/special chars with underscore
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', obj_name)
        lines.append(f'o {safe_name}\n')
        for v in verts:
            lines.append(f'v {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}\n')
        for idxs in tri_indices:
            # OBJ faces are 1-based and globally indexed
            lines.append(f'f {idxs[0]+vertex_offset+1} {idxs[1]+vertex_offset+1} {idxs[2]+vertex_offset+1}\n')

        vertex_offset += len(verts)
        obj_count += 1

    if obj_count == 0:
        print(f'[WARN] create_obj_bundle: no valid meshes, skipping {output_obj_path}')
        return

    with open(output_obj_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'[INFO] Created OBJ bundle: {output_obj_path} ({obj_count} objects)')


def create_obj_from_items(items, output_obj_path):
    """
    Write a Wavefront OBJ file directly from box dimension data — no temp STL files needed.
    items: list of dicts with keys: name, x, y, z, z_offset (all in mm).
    Each item becomes a named 'o' group = rectangular box (8 verts, 12 triangular faces).
    Works regardless of MESH_STORAGE mode.
    """
    lines = ['# ElevateFoods AI Nutrition Chatbot – multi-part food mesh\n']
    vertex_offset = 0
    obj_count = 0

    for item in items:
        iname  = item['name']
        ix     = float(item['x'])         # width mm
        iy     = float(item['y'])         # depth mm
        iz     = float(item['z'])         # height mm
        z0     = float(item.get('z_offset', 0.0))
        z1     = z0 + iz
        hx, hy = ix / 2.0, iy / 2.0

        verts = [
            (-hx, -hy, z0), ( hx, -hy, z0), ( hx,  hy, z0), (-hx,  hy, z0),
            (-hx, -hy, z1), ( hx, -hy, z1), ( hx,  hy, z1), (-hx,  hy, z1),
        ]
        faces = [
            (0,3,1),(1,3,2),  # bottom
            (4,5,6),(4,6,7),  # top
            (0,4,7),(0,7,3),  # left
            (5,1,2),(5,2,6),  # right
            (0,1,5),(0,5,4),  # front
            (2,3,7),(2,7,6),  # back
        ]

        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', iname)
        lines.append(f'o {safe_name}\n')
        for v in verts:
            lines.append(f'v {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}\n')
        for fi in faces:
            lines.append(f'f {fi[0]+vertex_offset+1} {fi[1]+vertex_offset+1} {fi[2]+vertex_offset+1}\n')
        vertex_offset += len(verts)
        obj_count += 1

    if obj_count == 0:
        print(f'[WARN] create_obj_from_items: no items, skipping {output_obj_path}')
        return

    with open(output_obj_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'[INFO] Written {obj_count} objects to {output_obj_path}')


def recommend(gender, age, height, weight, carbohydrate, protein, fat, activity, diet, preference, preferred_foods=''):
    ensure_nutrition_csv_fresh()

    targets = calculate_macro_targets(gender, age, height, weight, carbohydrate, protein, fat, activity, diet)
    calories = targets['calories']
    carbohydrate_intake = targets['carbohydrate_intake']
    protein_intake = targets['protein_intake']
    fat_intake = targets['fat_intake']
    carbohydrate_needed = targets['carbohydrate_needed']
    protein_needed = targets['protein_needed']
    fat_needed = targets['fat_needed']

    # Each row is [carbohydrates, proteins, fats]
    W_per_hundred = np.array([
        [17, 1.56, 0.05],  # PSP
        [11.2, 6.6, 0.61],   # Red Lentils
        [1.4, 1.38, 12.1],  # Avocado
        [0.06, 19.8, 1.15]   # Chicken Breast
    ])

    W = W_per_hundred * 0.01

    name = ['Purple Sweet Potato', 'Red Lentils', 'Avocado', 'Chicken Breast']
    density = [0.81, 1.182, 0.63, 0.82]
    
    if preference: blocked = 3
    else: blocked = 1

    y = np.array([carbohydrate_needed, protein_needed, fat_needed]) # [carbohydrates, proteins, fats]

    def _compute_best_matches(target_need, top_k=4, preferred_foods_text=''):
        """Build 3-4 nutrition-first options from CSV foods (fallback to model foods if needed)."""
        positive_target = np.maximum(target_need, 0.0)
        if np.all(positive_target <= 1e-6):
            return [], {'insufficient': False, 'suggested_foods': []}

        preferred_terms = [
            normalize_food_name(t.strip()) for t in str(preferred_foods_text or '').split(',') if t.strip()
        ]

        def _row_float(row, *keys, default=0.0):
            for key in keys:
                raw = str(row.get(key, '') or '').strip()
                if not raw:
                    continue
                try:
                    return float(raw)
                except Exception:
                    continue
            return default

        def _looks_non_veg(food_name_text):
            n = (food_name_text or '').lower()
            tags = ['chicken', 'beef', 'pork', 'fish', 'shrimp', 'mutton', 'lamb', 'meat', 'tuna', 'salmon']
            return any(t in n for t in tags)

        def _food_tags(food_name_text, vec):
            """Heuristic tags for dish composition quality."""
            n = (food_name_text or '').lower()
            carbs_pg, protein_pg, fat_pg = float(vec[0]), float(vec[1]), float(vec[2])

            tags = set()
            if carbs_pg >= max(protein_pg, fat_pg) and carbs_pg > 0.06:
                tags.add('base')
            if protein_pg >= max(carbs_pg, fat_pg) and protein_pg > 0.06:
                tags.add('protein')
            if fat_pg >= max(carbs_pg, protein_pg) and fat_pg > 0.05:
                tags.add('fat')

            veggie_keys = ['broccoli', 'spinach', 'cabbage', 'pepper', 'carrot', 'onion', 'tomato', 'mushroom', 'zucchini', 'lettuce', 'bean', 'pea', 'corn', 'eggplant', 'cauliflower']
            if any(k in n for k in veggie_keys):
                tags.add('veg')

            starch_keys = ['rice', 'noodle', 'pasta', 'potato', 'sweet potato', 'quinoa', 'oat', 'bread']
            if any(k in n for k in starch_keys):
                tags.add('base')

            protein_keys = ['chicken', 'beef', 'pork', 'fish', 'shrimp', 'tofu', 'egg', 'lentil', 'bean', 'turkey']
            if any(k in n for k in protein_keys):
                tags.add('protein')

            sauce_fat_keys = ['olive', 'avocado', 'sesame', 'cheese', 'nuts', 'peanut']
            if any(k in n for k in sauce_fat_keys):
                tags.add('fat')

            sweet_keys = ['candy', 'chocolate', 'cake', 'cookie', 'soda', 'syrup']
            if any(k in n for k in sweet_keys):
                tags.add('dessert')

            snack_keys = ['chips', 'popcorn', 'cracker', 'biscuit', 'snack']
            if any(k in n for k in snack_keys):
                tags.add('snack')

            return tags

        def _dish_quality(chosen_items):
            """Score whether selected foods can form one savory dish."""
            union_tags = set()
            names = []
            for it in chosen_items:
                union_tags.update(it.get('tags', set()))
                names.append(it.get('name', ''))

            score = 0.0
            if 'base' in union_tags:
                score += 1.2
            if 'protein' in union_tags:
                score += 1.6
            if 'veg' in union_tags:
                score += 1.0
            if 'fat' in union_tags:
                score += 0.6
            if {'base', 'protein', 'veg'}.issubset(union_tags):
                score += 2.0
            if 'dessert' in union_tags:
                score -= 2.5

            name_blob = ' '.join(n.lower() for n in names)
            if ('rice' in name_blob and ('chicken' in name_blob or 'tofu' in name_blob or 'egg' in name_blob)):
                score += 0.8
            if ('noodle' in name_blob and ('beef' in name_blob or 'chicken' in name_blob or 'tofu' in name_blob)):
                score += 0.8
            if ('potato' in name_blob and ('chicken' in name_blob or 'bean' in name_blob or 'lentil' in name_blob)):
                score += 0.6

            # Build short human-readable dish hint
            if {'base', 'protein', 'veg'}.issubset(union_tags):
                hint = 'Balanced one-bowl meal'
            elif {'protein', 'veg'}.issubset(union_tags):
                hint = 'Savory protein + veggie plate'
            elif {'base', 'protein'}.issubset(union_tags):
                hint = 'Hearty base + protein dish'
            else:
                hint = 'Simple mixed dish'

            return score, hint

        def _pool_from_csv():
            pool = []
            for row in (csv_data or []):
                fname = (row.get('category_name') or '').strip()
                if not fname:
                    continue
                lower_name = fname.lower()
                if normalize_food_name(fname) == 'background':
                    continue
                if preference and _looks_non_veg(fname):
                    continue
                if any(nk in lower_name for nk in ['almond', 'walnut', 'cashew', 'pecan', 'hazelnut', 'pistachio']):
                    continue

                carbs_pg = _row_float(row, 'carbohydrates', 'Carbohydrates', 'carbs', 'Carbs', default=0.0)
                protein_pg = _row_float(row, 'protein', 'Protein', default=0.0)
                fat_pg = _row_float(row, 'fat', 'Fat', default=0.0)
                vec = np.array([carbs_pg, protein_pg, fat_pg], dtype=float)
                if np.sum(vec) <= 1e-8:
                    continue
                tags = _food_tags(fname, vec)
                if 'dessert' in tags or 'snack' in tags:
                    continue
                if not ({'base', 'protein', 'veg', 'fat'} & tags):
                    continue
                norm_name = normalize_food_name(fname)
                pool.append({'name': fname, 'vec': vec, 'tags': tags, 'normalized': norm_name})
            return pool

        def _pool_from_model_foods():
            p = []
            for i in range(len(name)):
                if i == blocked:
                    continue
                p.append({'name': name[i], 'vec': W[i], 'tags': _food_tags(name[i], W[i])})
            return p

        def _is_preferred(item):
            if not preferred_terms:
                return True
            n = item.get('normalized') or normalize_food_name(item.get('name', ''))
            n = n.replace('_', ' ')
            for t in preferred_terms:
                if not t:
                    continue
                tt = t.replace('_', ' ')
                if n == tt or tt in n or n in tt:
                    return True
            return False

        def _select_shortlist(pool, k=18):
            scored = []
            for item in pool:
                probe = item['vec'] * 150.0  # 150g probe serving
                under = np.maximum(positive_target - probe, 0.0)
                over = np.maximum(probe - positive_target, 0.0)
                score = float(np.sum(under) + 3.0 * np.sum(over))
                scored.append((score, item))
            scored.sort(key=lambda x: x[0])
            return [item for _, item in scored[:k]]

        full_pool = _pool_from_csv()
        preferred_pool = [it for it in full_pool if _is_preferred(it)] if preferred_terms else full_pool
        using_preferred = bool(preferred_terms) and len(preferred_pool) >= 2

        pool = preferred_pool if len(preferred_pool) >= 2 else full_pool
        if len(pool) < 6:
            pool = _pool_from_model_foods()
            using_preferred = False

        shortlist = _select_shortlist(pool, k=18 if len(pool) > 18 else len(pool))
        if len(shortlist) < 2:
            return [], {
                'insufficient': bool(preferred_terms),
                'used_preferred': using_preferred,
                'preferred_foods': preferred_terms,
                'reason': 'Not enough preferred foods found in CSV to form combinations.',
                'suggested_foods': [it['name'] for it in _select_shortlist(full_pool, k=5)] if full_pool else [],
            }

        candidates = []
        combo_sizes = [2, 3] if len(shortlist) >= 3 else [2]
        for csize in combo_sizes:
            for idx_tuple in combinations(range(len(shortlist)), csize):
                chosen = [shortlist[i] for i in idx_tuple]
                nutr = np.array([it['vec'] for it in chosen], dtype=float)  # per-gram macros
                try:
                    grams, _ = nnls(nutr.T, positive_target)
                except Exception:
                    continue

                grams = np.clip(grams, 0.0, 350.0)
                if np.sum(grams >= 1.0) < 2:
                    continue

                supplied = np.dot(grams, nutr)
                under = np.maximum(positive_target - supplied, 0.0)
                over = np.maximum(supplied - positive_target, 0.0)
                dish_bonus, dish_hint = _dish_quality(chosen)
                if dish_bonus < 1.4:
                    continue
                score = float(np.sum(under) + 3.0 * np.sum(over) + 0.001 * np.sum(grams) - 0.7 * dish_bonus)

                foods = []
                for j, it in enumerate(chosen):
                    g = round(float(grams[j]), 2)
                    if g >= 1.0:
                        foods.append({'name': it['name'], 'gram': g})
                if len(foods) < 2:
                    continue

                candidates.append({
                    'foods': foods,
                    'dish_hint': dish_hint,
                    'supplied': {
                        'carbs': round(float(supplied[0]), 2),
                        'protein': round(float(supplied[1]), 2),
                        'fat': round(float(supplied[2]), 2),
                    },
                    'shortfall_total': round(float(np.sum(under)), 2),
                    'exceed_total': round(float(np.sum(over)), 2),
                    'dish_score': round(float(dish_bonus), 3),
                    'score': round(score, 3),
                })

        candidates.sort(key=lambda c: (c['score'], c['exceed_total'], c['shortfall_total'], -c.get('dish_score', 0.0)))

        selected = []
        seen_keys = set()
        for c in candidates:
            key = tuple(sorted(f['name'] for f in c['foods']))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            selected.append(c)
            if len(selected) >= top_k:
                break

        if len(selected) < min(3, top_k):
            for c in candidates:
                if c in selected:
                    continue
                selected.append(c)
                if len(selected) >= top_k:
                    break

        selected = selected[:top_k]

        advice = {
            'insufficient': False,
            'used_preferred': using_preferred,
            'preferred_foods': preferred_terms,
            'suggested_foods': []
        }

        if preferred_terms and not using_preferred:
            advice['insufficient'] = True
            advice['reason'] = 'Preferred foods were not enough to build complete combinations.'
            advice['suggested_foods'] = [it['name'] for it in _select_shortlist(full_pool, k=6)] if full_pool else []
            return selected, advice

        if using_preferred:
            best_gap = selected[0]['shortfall_total'] if selected else float(np.sum(positive_target))
            if (not selected) or best_gap > 18.0:
                advice['insufficient'] = True
                advice['reason'] = 'Preferred foods alone cannot closely meet required nutrition.'

                deficit = positive_target.copy()
                if selected:
                    s0 = selected[0].get('supplied', {})
                    deficit = np.maximum(
                        positive_target - np.array([
                            float(s0.get('carbs', 0) or 0),
                            float(s0.get('protein', 0) or 0),
                            float(s0.get('fat', 0) or 0),
                        ]),
                        0.0
                    )

                extra_pool = [it for it in full_pool if not _is_preferred(it)]
                scored_extra = []
                for it in extra_pool:
                    probe = it['vec'] * 100.0
                    fit = float(np.dot(probe, deficit))
                    if fit > 0:
                        scored_extra.append((fit, it['name']))
                scored_extra.sort(key=lambda x: x[0], reverse=True)
                advice['suggested_foods'] = [n for _, n in scored_extra[:6]]

        return selected, advice

    best_matches, best_match_advice = _compute_best_matches(y, top_k=4, preferred_foods_text=preferred_foods)

    # positive_indices = np.where(y > 0)[0]
    # positive_y = y[positive_indices]
    # positive_y = y
    # positive_y[positive_y <= 0] = 0
    
    nonlinear_constraint = NonlinearConstraint(constraint_func, 0.01, np.inf)

    solutions = []
    best_candidate = None  # fallback if nothing meets tolerance

    # def fun(amounts, nutritional_matrix, target):
    #     amounts = amounts.reshape(-1, 1)
    #     total_nutrition = np.dot(nutritional_matrix.T, amounts)
    #     return np.linalg.norm(total_nutrition - target)

    upper_bound = 10           # set None for “no limit”
    tolerance   = 1e-6

    def solve_pair(selected_nutrition, positive_y):
        # A = np.array([[nutrients[name1][c], nutrients[name2][c]] for c in cols], dtype=float) #selected_nutrition
        # Least-squares solution (satisfies A @ x ≈ b in L2 sense)
        # x, residuals, _, _ = np.linalg.lstsq(selected_nutrition.T, positive_y, rcond=None)
        x, res_norm = nnls(selected_nutrition.T, positive_y)
        residual = np.linalg.norm(selected_nutrition.T @ x - positive_y, ord=1)     # total absolute error
        return x, residual

    if np.any(y > 0):
        mask = y > 0  
        for indices in combinations(range(4), 2):
            if (blocked in indices): continue
            selected_nutrition = W[list(indices)]

            fun = lambda x: np.linalg.norm(selected_nutrition.T[mask, :] @ x - y[mask])
            res = minimize(fun, np.zeros(len(indices)), method='L-BFGS-B', bounds=[(0., MAX_VOLUME / density[indices[x]]) for x in range(len(indices))])

            print(f"Testing combination {indices}: amounts={res.x}, error={res.fun}")
            # Track best candidate even if above tolerance
            if res.x[0] > 0 and res.x[1] > 0:
                if best_candidate is None or res.fun < best_candidate[2]:
                    best_candidate = (indices, res.x, res.fun)

            # Accept solution if both amounts are positive and error is reasonable
            if res.x[0] > 0 and res.x[1] > 0 and res.fun < TOLERANCE:
                solutions.append((indices, res.x, res.fun))
                print(f"  -> ACCEPTED")
            else:
                print(f"  -> REJECTED (tolerance={TOLERANCE})")

        # If none accepted, use best candidate so we always produce meshes
        if not solutions and best_candidate:
            solutions.append(best_candidate)
            print(f"\nNo solutions under tolerance. Using best available combination with error={best_candidate[2]:.2f}")

        solutions.sort(key=lambda x: x[2])
        print(f"\n=== Found {len(solutions)} valid solutions ===")
    
    # Limit number of solutions to avoid long runtimes / memory use
    solutions = solutions[:MAX_SOLUTIONS]
    results = []

    for index in range(len(solutions)):
        indices, amounts, norm = solutions[index]
        material_mesh_list = []
        carbohydrate_supplement = protein_supplement = fat_supplement = 0
        cumulative_z = 0.0  # running z offset so each food item stacks on top of the previous
        # print(amounts)
        for i in range(len(amounts)):             
            amounts[i] = round(amounts[i], 2)
            if amounts[i] == 0: continue
            mesh_name = str(index) + "_" + name[indices[i]] + ".stl"
            carbohydrate_supplement += amounts[i] * W[indices[i]][0]
            protein_supplement += amounts[i] * W[indices[i]][1]
            fat_supplement += amounts[i] * W[indices[i]][2]
            z_off = cumulative_z
            # Record manifest for on-demand regeneration, regardless of generation mode
            try:
                manifest = _load_manifest()
                manifest[mesh_name] = { 'amount': float(amounts[i]), 'density': float(density[indices[i]]), 'z_offset': float(z_off) }
                _save_manifest(manifest)
            except Exception as mf_err:
                print(f"[WARN] Failed to update manifest for {mesh_name}: {mf_err}")

            # Decide whether to generate STL based on MESH_MODE
            generate_mesh = (MESH_MODE == 'all') or (MESH_MODE == 'first' and index == 0)
            x, y, z = mesh_generation(mesh_name, amounts[i], density[indices[i]], z_offset=z_off) if generate_mesh else calculate_cube_dimension(amounts[i] / density[indices[i]])
            # Show download links when meshes are allowed; on-demand regen will be used if file is missing
            mesh_field = mesh_name if MESH_MODE != 'none' and x and y and z else ''
            if x and y and z:
                material_mesh_list.append({'name': name[indices[i]], 'mesh': mesh_field, 'gram': amounts[i],
                                           'x': round(x, 2), 'y': round(y, 2), 'z': round(z, 2),
                                           'z_offset': round(z_off, 4)})
                cumulative_z += z  # advance the stack by this item's thickness
        # Folder name hint for client-side direct folder save (no ZIP packaging).
        folder_name = ''
        obj_name = ''
        if MESH_MODE != 'none' and material_mesh_list:
            folder_name = f"{datetime.now().strftime('%Y%m%d')}_option{index + 1}"

        # Build one stacked OBJ per option so all blocks can be opened as a single model.
        if material_mesh_list:
            try:
                obj_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_option{index + 1}_{uuid.uuid4().hex[:6]}.obj"
                obj_path = os.path.join(tempfile.gettempdir(), obj_name)
                create_obj_from_items(material_mesh_list, obj_path)
            except Exception as obj_err:
                print(f"[WARN] Failed to create OBJ for option {index + 1}: {obj_err}")
                obj_name = ''

        results.append((material_mesh_list, round(carbohydrate_supplement, 2), round(protein_supplement, 2), round(fat_supplement, 2), folder_name, obj_name))

    # print(results)

    recommend_dict = {'calories': round(calories, 2), 
                      'carbohydrate_intake': round(carbohydrate_intake, 2),
                      'protein_intake': round(protein_intake, 2),
                      'fat_intake': round(fat_intake, 2),
                      'carbohydrate_needed': round(carbohydrate_needed, 2),
                      'protein_needed': round(protein_needed, 2),
                      'fat_needed': round(fat_needed, 2),
                                            'best_matches': best_matches,
                                            'best_match_advice': best_match_advice,
                    #   'carbohydrate_supplement': round(carbohydrate_needed, 2),
                    #   'protein_supplement': round(protein_needed, 2),
                    #   'fat_supplement': round(fat_needed, 2),
                      'results': results
                    }
    
    return recommend_dict
 
@app.route('/data_collection', methods=["GET", "POST"])
def data_collection():
    # carbs = float(request.args.get('carbs'))
    # protein = float(request.args.get('protein'))
    # fat = float(request.args.get('fat'))
    if request.method == "POST":
        # print(request.form)
        submit = request.form["submit"]
        info_dict = {
            'gender': int(request.form["gender"]),
            'age': int(request.form["age"]),
            'height': float(request.form["height"]), 
            'weight': float(request.form["weight"]), 
            'carbs': float(request.form["carbohydrate"]),
            'protein': float(request.form["protein"]),
            'fat': float(request.form["fat"]),
            'activity': int(request.form["activity"]),
            'diet': int(request.form["diet"]),
            'preference': int(request.form["preference"]),
        }
        
        if submit: return redirect(url_for("nutrition_recommendation_display", info_dict=info_dict))
    # return render_template("data-collection.html", carbs=carbs, protein=protein, fat=fat)
    return render_template("data-collection.html")

def list2dict(info_dict):
    new_dict = {}
    for d in info_dict:
        index, value = d.split(':')

        if '{' in index: index = index[2:-1]
        else: index = index[2:-1]

        if '}' in value: value = value[:-1]
        else: value = value

        new_dict[index] = float(value)
    
    return new_dict

@app.route('/download/<path:filename>', methods=['GET', 'POST'])
def download(filename):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    
    blob_path = os.path.join("/meshes", filename)
    blob = bucket.blob(blob_path)

    file_stream = io.BytesIO()
    blob.download_to_file(file_stream)
    file_stream.seek(0)

    return send_file(file_stream, as_attachment=True, download_name=filename)

@app.route('/nutrition_recommendation_display', methods=["GET", "POST"])
def nutrition_recommendation_display():
    info_dict = request.args.get('info_dict')
    
    # If no info_dict provided, use default values for demo
    if info_dict is None:
        # Use default values as a dict directly
        info_dict = {
            'gender': 0,
            'age': 25,
            'height': 170.0,
            'weight': 70.0,
            'carbs': 0.0,
            'protein': 0.0,
            'fat': 0.0,
            'activity': 2,
            'diet': 0,
            'preference': 0
        }
    else:
        info_dict = info_dict.split(",")
        info_dict = list2dict(info_dict)
    
    # print(info_dict)
    recommend_dict = recommend(int(info_dict['gender']), int(info_dict['age']), info_dict['height'], info_dict['weight'], info_dict['carbs'], \
                               info_dict['protein'], info_dict['fat'], int(info_dict['activity']), int(info_dict['diet']), int(info_dict['preference']))

    # print(recommend_dict)
    
    if request.method == "POST":
        # print(request.form)
        refresh = request.form["refresh"]
        # if refresh: return redirect("/upload_image")
        if refresh: return redirect("/data_collection")
    return render_template("nutrition-recommendation.html", recommend_dict=recommend_dict)

# Chatbot routes
@app.route('/chatbot', methods=["GET", "POST"])
def chatbot():
    """Serve the chatbot interface"""
    return render_template("chatbot.html")

@app.route('/api/search-food', methods=['POST'])
def api_search_food():
    """Search for food in CSV data and return parsed nutrition. Supports multiple foods separated by commas."""
    try:
        data = request.json
        food_input = data.get('food_input', '').strip()
        
        if not food_input:
            return jsonify({'error': 'No food input provided'}), 400
        
        # Split by comma variants and semicolons to handle multiple foods
        food_items = [item.strip() for item in re.split(r'[,，;]+', food_input)]
        
        # Store all nutrition data and individual results
        total_nutrition = {
            'carbs': 0,
            'protein': 0,
            'fat': 0,
            'calories': 0
        }
        individual_foods = []
        errors = []
        
        # Process each food item
        for food_item in food_items:
            if not food_item:
                continue

            # Handle direct macro entries in mixed input (e.g., "100g carb")
            direct_macro = parse_direct_macro_input(food_item)
            if direct_macro is not None:
                total_nutrition['carbs'] += direct_macro.get('carbs', 0)
                total_nutrition['protein'] += direct_macro.get('protein', 0)
                total_nutrition['fat'] += direct_macro.get('fat', 0)
                total_nutrition['calories'] += direct_macro.get('calories', 0)
                individual_foods.append(direct_macro)
                continue
                
            # Parse user input
            food_name, quantity, unit = parse_food_input(food_item)
            
            # Resolve nutrition with CSV-first + USDA fallback
            nutrition, source = get_food_nutrition_with_fallback(food_name, quantity, unit)
            if not nutrition:
                # Both CSV and USDA failed - ask user to search manually
                errors.append({
                    'food': food_item,
                    'message': f"'{food_item}' not found in local database or USDA API. Please search manually and enter nutrition values.",
                    'manual_input': True
                })
                continue
            
            if not nutrition:
                errors.append({
                    'food': food_item,
                    'message': f"Could not retrieve nutrition for '{food_item}'",
                    'manual_input': False
                })
                continue
            
            # Add source to nutrition data
            nutrition['source'] = source
            
            # Add to total nutrition
            total_nutrition['carbs'] += nutrition.get('carbs', 0)
            total_nutrition['protein'] += nutrition.get('protein', 0)
            total_nutrition['fat'] += nutrition.get('fat', 0)
            total_nutrition['calories'] += nutrition.get('calories', 0)
            
            # Store individual food info
            individual_foods.append({
                'food_name': nutrition.get('food_name', ''),
                'quantity': nutrition.get('quantity', 0),
                'unit': nutrition.get('unit', ''),
                'carbs': nutrition.get('carbs', 0),
                'protein': nutrition.get('protein', 0),
                'fat': nutrition.get('fat', 0),
                'calories': nutrition.get('calories', 0),
                'source': nutrition.get('source', 'Unknown')
            })
        
        # Check if we successfully processed at least one food
        if not individual_foods:
            if errors:
                # Separate errors into manual input needed vs other errors
                manual_input_foods = [e for e in errors if isinstance(e, dict) and e.get('manual_input')]
                other_errors = [e for e in errors if isinstance(e, dict) and not e.get('manual_input')]
                
                error_message = 'Could not find foods in any database'
                if manual_input_foods:
                    error_message += '. Please search the internet and manually enter nutrition values for: ' + ', '.join([e['food'] for e in manual_input_foods])
                
                return jsonify({
                    'error': error_message,
                    'all_errors': errors,
                    'manual_input_required': len(manual_input_foods) > 0,
                    'manual_input_foods': manual_input_foods
                }), 404
            return jsonify({
                'error': 'No valid food items provided',
                'suggestion': 'Please provide at least one food item'
            }), 400
        
        # Build response
        response = {
            'success': True,
            'nutrition': {
                'carbs': round(total_nutrition['carbs'], 2),
                'protein': round(total_nutrition['protein'], 2),
                'fat': round(total_nutrition['fat'], 2),
                'calories': round(total_nutrition['calories'], 2)
            },
            'individual_foods': individual_foods,
            'original_input': food_input,
            'foods_processed': len(individual_foods)
        }
        
        # Include warnings if some items failed
        if errors:
            manual_input_foods = [e for e in errors if isinstance(e, dict) and e.get('manual_input')]
            response['warnings'] = errors
            if manual_input_foods:
                response['manual_input_required'] = True
                response['manual_input_foods'] = manual_input_foods
        
        return jsonify(response), 200
    
    except Exception as e:
        print(f"Error in api_search_food: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/analyze-image-nutrition', methods=['POST'])
def api_analyze_image_nutrition():
    """Analyze one meal image and return nutrition directly for chatbot ingestion."""
    try:
        file = request.files.get('image')
        food_hints = str(request.form.get('food_hints', '') or '').strip()
        plate_diameter_cm = _safe_float(
            request.form.get('plate_diameter_cm', DEFAULT_PLATE_DIAMETER_CM),
            DEFAULT_PLATE_DIAMETER_CM,
        )

        if not file or not file.filename:
            return jsonify({'error': 'Please provide an image file.'}), 400

        filename = secure_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            ext = _uploaded_image_extension(file, filename)
        if not ext:
            return jsonify({'error': 'Supported image types: JPG, JPEG, PNG, WEBP, JFIF, BMP, TIFF.'}), 400

        saved_name = f"meal_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}"
        upload_dir = os.path.abspath(app.config['UPLOAD_FOLDER'])
        os.makedirs(upload_dir, exist_ok=True)
        save_path = os.path.join(upload_dir, saved_name)
        file.save(save_path)

        result = analyze_uploaded_meal_image(
            image_path=save_path,
            food_hints_text=food_hints,
            plate_diameter_cm=plate_diameter_cm,
        )

        nutrition = result.get('nutrition', {}) or {}
        food_items = nutrition.get('food_items', []) or []

        return jsonify({
            'success': True,
            'image_origin': result.get('image_origin', ''),
            'analysis_source': nutrition.get('analysis_source', 'heuristic-segmentation'),
            'nutrition': {
                'calories': round(float(nutrition.get('calories', 0) or 0), 2),
                'carbs': round(float(nutrition.get('carbs', 0) or 0), 2),
                'protein': round(float(nutrition.get('protein', 0) or 0), 2),
                'fat': round(float(nutrition.get('fat', 0) or 0), 2),
            },
            'food_items': [
                {
                    'food_id': int(item.get('food_id', 0) or 0),
                    'food_name': str(item.get('food_name', 'unknown food') or 'unknown food'),
                    'weight_g': round(float(item.get('weight_g', 0) or 0), 2),
                    'volume_cm3': round(float(item.get('volume_cm3', 0) or 0), 2),
                    'calories': round(float(item.get('calories', 0) or 0), 2),
                    'carbs': round(float(item.get('carbs', 0) or 0), 2),
                    'protein': round(float(item.get('protein', 0) or 0), 2),
                    'fat': round(float(item.get('fat', 0) or 0), 2),
                }
                for item in food_items
            ]
        }), 200
    except Exception as e:
        print(f"Error in api_analyze_image_nutrition: {e}")
        if DIAG_MODE:
            return jsonify({'error': str(e)}), 500
        return jsonify({'error': 'Image nutrition analysis failed.'}), 500
@app.route('/api/calculate-recommendation', methods=['POST'])
def api_calculate_recommendation():
    """Calculate nutrition recommendation based on user info and daily intake"""
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        user_id = data.get('user_id')
        user_info = data.get('user_info', {})
        daily_nutrition = data.get('daily_nutrition', {})
        if not isinstance(user_info, dict):
            user_info = {}
        if not isinstance(daily_nutrition, dict):
            daily_nutrition = {}

        if not data:
            return jsonify({'error': 'Request body missing. Send JSON with user_info and daily_nutrition.'}), 400

        def to_int(val, default=0):
            try:
                # Treat "" or None as missing -> default
                if val is None or val == '':
                    return default
                return int(val)
            except Exception:
                return default

        def to_float(val, default=0.0):
            try:
                if val is None or val == '':
                    return default
                return float(val)
            except Exception:
                return default

        # Extract values with safe coercion
        gender = to_int(user_info.get('gender'), 0)
        age = to_int(user_info.get('age'), 0)
        height = to_float(user_info.get('height'), 0)
        weight = to_float(user_info.get('weight'), 0)
        carbs = to_float(daily_nutrition.get('carbs', daily_nutrition.get('carbohydrate')), 0)
        protein = to_float(daily_nutrition.get('protein'), 0)
        fat = to_float(daily_nutrition.get('fat'), 0)
        activity = to_int(user_info.get('activity'), 0)
        diet = to_int(user_info.get('diet'), 0)
        preference = to_int(user_info.get('preference'), 0)
        preferred_foods = str(user_info.get('preferred_foods', '') or '').strip()

        # Minimal validation with soft defaults
        missing = []
        if gender not in [0, 1]: missing.append('gender')
        if age <= 0: missing.append('age')
        if height <= 0: missing.append('height')
        if weight <= 0: missing.append('weight')
        if activity not in [0, 1, 2, 3]: missing.append('activity')
        if diet not in [0, 1, 2, 3]: missing.append('diet')
        if preference not in [0, 1]: missing.append('preference')

        # If missing, apply sensible defaults to keep API responsive
        if missing:
            defaults = {
                'gender': 0,
                'age': 25,
                'height': 170.0,
                'weight': 70.0,
                'activity': 2,
                'diet': 0,
                'preference': 0,
            }
            gender = gender if gender in [0,1] else defaults['gender']
            age = age if age > 0 else defaults['age']
            height = height if height > 0 else defaults['height']
            weight = weight if weight > 0 else defaults['weight']
            activity = activity if activity in [0,1,2,3] else defaults['activity']
            diet = diet if diet in [0,1,2,3] else defaults['diet']
            preference = preference if preference in [0,1] else defaults['preference']
            note = f"Applied defaults for: {', '.join(missing)}"
        else:
            note = None
        
        # Call existing recommendation function with a robust fallback
        try:
            recommend_dict = recommend(gender, age, height, weight, carbs, protein, fat, activity, diet, preference, preferred_foods)
            if not isinstance(recommend_dict, dict):
                raise ValueError('recommend() returned an invalid payload')
        except Exception as rec_err:
            # Fallback: compute targets and needs without optimization/meshes
            try:
                rmr = calculate_rmr(weight, height, age, gender)
                calories = calculate_daily_calories(rmr, activity)
                diet_scale = [
                    (0.50 / 4.1, 0.20 / 4.1, 0.30 / 8.8),  # balanced
                    (0.60 / 4.1, 0.20 / 4.1, 0.20 / 8.8),  # low fat
                    (0.20 / 4.1, 0.30 / 4.1, 0.50 / 8.8),  # low carbs
                    (0.28 / 4.1, 0.39 / 4.1, 0.33 / 8.8),  # high protein
                ]
                carbohydrate_intake, protein_intake, fat_intake = (calories * i for i in diet_scale[diet])
                recommend_dict = {
                    'calories': round(calories, 2),
                    'carbohydrate_intake': round(carbohydrate_intake, 2),
                    'protein_intake': round(protein_intake, 2),
                    'fat_intake': round(fat_intake, 2),
                    'carbohydrate_needed': round(carbohydrate_intake - carbs, 2),
                    'protein_needed': round(protein_intake - protein, 2),
                    'fat_needed': round(fat_intake - fat, 2),
                    'best_matches': [],
                    'best_match_advice': {'insufficient': False, 'suggested_foods': []},
                    'results': []
                }
                note = 'Generated minimal recommendation (optimization failed)'
                if DIAG_MODE:
                    recommend_dict.update({'error': str(rec_err)})
            except Exception as fb_err:
                print(f"Fallback generation failed: {fb_err}")
                if DIAG_MODE:
                    return jsonify({'error': f'Fallback failed: {fb_err}'}), 500
                raise rec_err

        if note:
            recommend_dict['note'] = note
        
        saved_record = save_user_record(
            user_id=user_id,
            user_info={
                'gender': gender,
                'age': age,
                'height': height,
                'weight': weight,
                'activity': activity,
                'diet': diet,
                'preference': preference,
                'preferred_foods': preferred_foods,
            },
            daily_nutrition={
                'carbs': carbs,
                'protein': protein,
                'fat': fat,
            },
            recommendation=recommend_dict
        )

        return jsonify({
            'success': True,
            'user_id': saved_record.get('user_id'),
            'recommendation': recommend_dict
        }), 200
    
    except Exception as e:
        print(f"Error in api_calculate_recommendation: {e}")
        if DIAG_MODE:
            return jsonify({'error': str(e)}), 500
        return jsonify({'error': 'Recommendation failed. Please try again later.'}), 500


@app.route('/api/calculate-custom-recipes', methods=['POST'])
def api_calculate_custom_recipes():
    """Calculate Recipe 1-4 from user-entered desired foods."""
    try:
        data = request.json or {}
        user_info = data.get('user_info', {})
        daily_nutrition = data.get('daily_nutrition', {})
        food_text = str(data.get('food_text', '') or '').strip()

        if not food_text:
            return jsonify({'error': 'Please enter foods like "chicken breast, broccoli, noodles".'}), 400

        def to_int(val, default=0):
            try:
                if val is None or val == '':
                    return default
                return int(val)
            except Exception:
                return default

        def to_float(val, default=0.0):
            try:
                if val is None or val == '':
                    return default
                return float(val)
            except Exception:
                return default

        gender = to_int(user_info.get('gender'), 0)
        age = to_int(user_info.get('age'), 25)
        height = to_float(user_info.get('height'), 170.0)
        weight = to_float(user_info.get('weight'), 70.0)
        carbs = to_float(daily_nutrition.get('carbs'), 0)
        protein = to_float(daily_nutrition.get('protein'), 0)
        fat = to_float(daily_nutrition.get('fat'), 0)
        activity = to_int(user_info.get('activity'), 2)
        diet = to_int(user_info.get('diet'), 0)
        preference = to_int(user_info.get('preference'), 0)

        targets = calculate_macro_targets(gender, age, height, weight, carbs, protein, fat, activity, diet)
        recipe_data = build_custom_recipe_recommendations(food_text, targets['need_vector'], preference, limit=4)

        return jsonify({
            'success': True,
            'recipes': recipe_data['recipes'],
            'requested_foods': recipe_data['requested_foods'],
            'resolved_foods': recipe_data['resolved_foods'],
            'unresolved_foods': recipe_data['unresolved_foods'],
            'advice': recipe_data['advice'],
        }), 200
    except Exception as e:
        print(f"Error in api_calculate_custom_recipes: {e}")
        if DIAG_MODE:
            return jsonify({'error': str(e)}), 500
        return jsonify({'error': 'Custom recipe calculation failed.'}), 500


@app.route('/api/update-image-food-label', methods=['POST'])
def api_update_image_food_label():
    """Relabel one detected image region and recompute meal nutrition totals."""
    try:
        payload = request.get_json(silent=True) or {}
        image_name = str(payload.get('image_name', '') or '').strip()
        region_id = int(payload.get('region_id', 0) or 0)
        food_name = str(payload.get('food_name', '') or '').strip()

        if not image_name:
            return jsonify({'error': 'image_name is required.'}), 400
        if region_id <= 0:
            return jsonify({'error': 'region_id must be > 0.'}), 400
        if not food_name:
            return jsonify({'error': 'food_name is required.'}), 400

        # Basic path safety
        safe_image_name = os.path.basename(image_name)
        if safe_image_name != image_name:
            return jsonify({'error': 'Invalid image_name.'}), 400

        base_dir = os.path.join('.', 'static', 'foodseg', safe_image_name)
        hover_path = os.path.join(base_dir, f'{safe_image_name}_hover.json')
        nutrition_path = os.path.join(base_dir, f'{safe_image_name}_nutrition.json')

        if not os.path.exists(hover_path):
            return jsonify({'error': 'Hover metadata not found for this image.'}), 404

        with open(hover_path, 'r', encoding='utf-8') as f:
            hover_data = json.load(f)

        hover_items = hover_data.get('hover_items', []) or []
        target = None
        for item in hover_items:
            if int(item.get('id', 0) or 0) == region_id:
                target = item
                break

        if target is None:
            return jsonify({'error': f'Region {region_id} not found.'}), 404

        default_profile = _default_food_profile()
        profile = _food_profile_from_name(food_name, default_profile=default_profile)

        weight_g = float(target.get('weight_g', 0) or 0)
        density = max(float(profile.get('density', default_profile['density']) or default_profile['density']), 0.1)
        calories = round(weight_g * float(profile['calories_pg']), 2)
        carbs = round(weight_g * float(profile['carbs_pg']), 2)
        protein = round(weight_g * float(profile['protein_pg']), 2)
        fat = round(weight_g * float(profile['fat_pg']), 2)
        volume_cm3 = round(weight_g / density, 2)

        target['food_name'] = profile['food_name']
        target['calories'] = calories
        target['carbs'] = carbs
        target['protein'] = protein
        target['fat'] = fat
        target['weight_g'] = round(weight_g, 2)
        target['volume_cm3'] = volume_cm3
        target['food_id'] = int(profile.get('food_id', 0) or 0)

        with open(hover_path, 'w', encoding='utf-8') as f:
            json.dump({'hover_items': hover_items}, f, ensure_ascii=True, indent=4)

        rebuilt_items = []
        for it in hover_items:
            rebuilt_items.append({
                'food_id': int(it.get('food_id', 0) or 0),
                'food_name': str(it.get('food_name', 'unknown food') or 'unknown food'),
                'volume_cm3': round(float(it.get('volume_cm3', 0) or 0), 2),
                'weight_g': round(float(it.get('weight_g', 0) or 0), 2),
                'calories': round(float(it.get('calories', 0) or 0), 2),
                'protein': round(float(it.get('protein', 0) or 0), 2),
                'fat': round(float(it.get('fat', 0) or 0), 2),
                'carbs': round(float(it.get('carbs', 0) or 0), 2),
                'fiber': 0.0,
                'sugars': 0.0,
            })

        aggregated_items = _aggregate_food_items(rebuilt_items)
        totals = {
            'total_volume_cm3': round(float(sum(x['volume_cm3'] for x in aggregated_items)), 2),
            'total_weight_g': round(float(sum(x['weight_g'] for x in aggregated_items)), 2),
            'calories': round(float(sum(x['calories'] for x in aggregated_items)), 2),
            'protein': round(float(sum(x['protein'] for x in aggregated_items)), 2),
            'fat': round(float(sum(x['fat'] for x in aggregated_items)), 2),
            'carbs': round(float(sum(x['carbs'] for x in aggregated_items)), 2),
            'fiber': 0.0,
            'sugars': 0.0,
            'food_items': aggregated_items,
        }

        with open(nutrition_path, 'w', encoding='utf-8') as f:
            json.dump(totals, f, ensure_ascii=True, indent=4)

        return jsonify({
            'success': True,
            'hover_items': hover_items,
            'nutrition': totals,
            'resolved_food_name': profile['food_name'],
            'food_found_in_csv': profile['found'],
        }), 200
    except Exception as e:
        print(f"Error in api_update_image_food_label: {e}")
        if DIAG_MODE:
            return jsonify({'error': str(e)}), 500
        return jsonify({'error': 'Failed to update image food label.'}), 500

@app.route('/api/user-records', methods=['GET', 'POST'])
def api_user_records():
    """Create/list backend user records for multi-user support."""
    try:
        if request.method == 'GET':
            records = _load_user_records()
            # Return full records for UI usage (including user_info for display)
            summaries = []
            for _, record in records.items():
                summaries.append({
                    'id': record.get('user_id'),
                    'user_id': record.get('user_id'),
                    'created_at': record.get('created_at'),
                    'updated_at': record.get('updated_at'),
                    'user_info': record.get('user_info', {}),
                    'history_count': len(record.get('history', []))
                })
            summaries.sort(key=lambda r: r.get('updated_at', ''), reverse=True)
            return jsonify({'success': True, 'records': summaries}), 200

        data = request.json or {}
        
        # Handle creation with just a name (from multi-user UI)
        if 'name' in data and 'user_id' not in data:
            user_info = {'name': data.get('name')}
            saved = save_user_record(user_id=None, user_info=user_info)
            user_id = saved.get('user_id')
            return jsonify({
                'success': True, 
                'user': {
                    'id': user_id,
                    'user_id': user_id,
                    'created_at': saved.get('created_at'),
                    'updated_at': saved.get('updated_at'),
                    'user_info': user_info
                }
            }), 200
        
        # Handle normal case (full user record update)
        user_id = data.get('user_id')
        user_info = data.get('user_info', {})
        saved = save_user_record(user_id=user_id, user_info=user_info)
        return jsonify({'success': True, 'record': saved}), 200
    except Exception as e:
        print(f"Error in api_user_records: {e}")
        if DIAG_MODE:
            return jsonify({'error': str(e)}), 500
        return jsonify({'error': 'User record operation failed.'}), 500

@app.route('/api/user-records/<user_id>', methods=['GET', 'DELETE'])
def api_user_record_detail(user_id):
    """Fetch one full user record including history, or delete a user."""
    try:
        if request.method == 'DELETE':
            # Delete user record
            records = _load_user_records()
            if user_id not in records:
                return jsonify({'error': 'User record not found'}), 404
            
            del records[user_id]
            _save_user_records(records)
            return jsonify({'success': True, 'message': 'User deleted'}), 200
        
        # GET request
        record = get_user_record(user_id)
        if not record:
            return jsonify({'error': 'User record not found'}), 404
        return jsonify({'success': True, 'record': record}), 200
    except Exception as e:
        print(f"Error in api_user_record_detail: {e}")
        if DIAG_MODE:
            return jsonify({'error': str(e)}), 500
        return jsonify({'error': 'Unable to fetch user record.'}), 500

@app.route('/download-obj/<path:filename>', methods=['GET'])
def download_obj(filename):
    """Download a pre-built .obj bundle from local temp storage."""
    try:
        temp_dir = tempfile.gettempdir()
        local_path = os.path.join(temp_dir, filename)
        if not os.path.exists(local_path):
            return jsonify({'error': f'OBJ file not found: {filename}'}), 404
        with open(local_path, 'rb') as f:
            file_data = io.BytesIO(f.read())
        file_data.seek(0)
        return send_file(
            file_data,
            mimetype='model/obj',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f'[ERROR] Error downloading OBJ: {e}')
        return jsonify({'error': str(e)}), 404


@app.route('/download-stl/<path:filename>', methods=['GET'])
def download_stl(filename):
    """Download STL file from Google Cloud Storage"""
    try:
        print(f"[DEBUG] Attempting to download STL file: {filename}")
        stl_bytes, err = _load_stl_bytes(filename)
        if err:
            return jsonify({'error': err}), 404

        file_data = io.BytesIO(stl_bytes)
        file_data.seek(0)
        print(f"[DEBUG] Serving STL {filename}, size: {file_data.getbuffer().nbytes} bytes")

        return send_file(
            file_data,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"[ERROR] Error downloading STL: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'File not found or download failed: {str(e)}'}), 404


def _is_safe_stl_filename(filename):
    base = os.path.basename(str(filename or ''))
    return bool(base) and base == filename and base.lower().endswith('.stl')


def _load_stl_bytes(filename):
    """Load one STL as bytes from local/GCS, regenerating local meshes when possible."""
    if not _is_safe_stl_filename(filename):
        return None, f'Invalid STL filename: {filename}'

    if MESH_STORAGE == 'local':
        temp_dir = tempfile.gettempdir()
        local_path = os.path.join(temp_dir, filename)
        if not os.path.exists(local_path):
            print(f"[DEBUG] Local STL not found at {local_path}, attempting regeneration from manifest")
            manifest = _load_manifest()
            meta = manifest.get(filename)
            if meta and 'amount' in meta and 'density' in meta:
                try:
                    mesh_generation(
                        filename,
                        float(meta['amount']),
                        float(meta['density']),
                        z_offset=float(meta.get('z_offset', 0.0))
                    )
                except Exception as regen_err:
                    print(f"[WARN] Regeneration failed: {regen_err}")
            if not os.path.exists(local_path):
                return None, f'File not found (local): {filename}'
        with open(local_path, 'rb') as f:
            return f.read(), None

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob_path = f"meshes/{filename}"
    blob = bucket.blob(blob_path)
    if not blob.exists():
        return None, f'File not found in storage: {filename}'
    out = io.BytesIO()
    blob.download_to_file(out)
    out.seek(0)
    return out.read(), None


@app.route('/download-stl-zip', methods=['POST'])
def download_stl_zip():
    """Bundle requested STL files into one ZIP for mobile/browser fallback."""
    try:
        payload = request.get_json(silent=True) or {}
        files = payload.get('files', []) or []
        folder_name = str(payload.get('folder_name', 'stl_files') or 'stl_files').strip()

        if not isinstance(files, list) or not files:
            return jsonify({'error': 'No STL files requested.'}), 400

        safe_files = []
        for f in files[:50]:
            fname = os.path.basename(str(f or '').strip())
            if _is_safe_stl_filename(fname):
                safe_files.append(fname)
        if not safe_files:
            return jsonify({'error': 'No valid STL filenames provided.'}), 400

        safe_folder = re.sub(r'[^a-zA-Z0-9_-]+', '_', folder_name).strip('_') or 'stl_files'

        zip_buffer = io.BytesIO()
        missing = []
        with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            for fname in safe_files:
                data, err = _load_stl_bytes(fname)
                if err or data is None:
                    missing.append(fname)
                    continue
                zf.writestr(fname, data)

            if missing:
                zf.writestr('README_missing_files.txt', 'Some files could not be included:\n' + '\n'.join(missing))

        zip_buffer.seek(0)
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'{safe_folder}.zip'
        )
    except Exception as e:
        print(f"[ERROR] Error creating STL ZIP: {e}")
        if DIAG_MODE:
            return jsonify({'error': str(e)}), 500
        return jsonify({'error': 'Failed to create STL ZIP.'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

@app.route('/api/daily-intake/<user_id>', methods=['GET', 'POST'])
def save_daily_intake(user_id):
    """Save or retrieve daily nutrition history for a user."""
    try:
        records = _load_user_records()
        if user_id not in records:
            return jsonify({'error': 'User not found'}), 404

        if request.method == 'GET':
            history = records[user_id].get('daily_history', [])
            return jsonify({'success': True, 'history': history}), 200

        data = request.get_json() or {}
        daily_nutrition = data.get('daily_nutrition', {})
        recommended = data.get('recommended', {})
        today = datetime.now().strftime('%Y-%m-%d')

        user = records[user_id]
        daily_history = user.setdefault('daily_history', [])

        # Update existing entry for today or append a new one
        today_entry = next((e for e in daily_history if e.get('date') == today), None)
        if today_entry:
            today_entry['nutrition'] = daily_nutrition
            if recommended:
                today_entry['recommended'] = recommended
            today_entry['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        else:
            entry = {
                'date': today,
                'nutrition': daily_nutrition,
                'updated_at': datetime.utcnow().isoformat() + 'Z'
            }
            if recommended:
                entry['recommended'] = recommended
            daily_history.append(entry)

        _save_user_records(records)
        return jsonify({'success': True, 'user_id': user_id}), 200
    except Exception as e:
        print(f"[ERROR] Failed to save/get daily intake: {e}")
        return jsonify({'error': str(e)}), 500
 
# main driver function
if __name__ == '__main__':
    # Run on 0.0.0.0 to allow external access (Cloudflare tunnel, network access, etc.)
    app.run(host="0.0.0.0", port=5000, debug=True)