from flask import Flask, flash, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
from datetime import datetime, timedelta, timezone
import hmac
import json
import logging
import os
import secrets
import tempfile
import urllib.request
from urllib.error import URLError

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=bool(os.environ.get('VERCEL')) or os.environ.get('FLASK_ENV') == 'production',
)
if not os.environ.get('FLASK_SECRET_KEY'):
    logging.getLogger(__name__).warning(
        "FLASK_SECRET_KEY is not configured; sessions will not survive a restart"
    )

# ────────────────────────────────
# 気象警報・注意報設定
REGION = {
    "name": "青森市",
    "prefecture_code": "020000",
    "area_code": "0220100",
    "map_center": [40.8222, 140.7474],
}
STAFF_PASSWORD = '123'
WARNING_URL = f"https://www.jma.go.jp/bosai/warning/data/r8/{REGION['prefecture_code']}.json"
JMA_SOURCE = "気象庁"

JST = timezone(timedelta(hours=9))

WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}
WARNING_KIND_BY_CODE = {
    **{code: "special_warning" for code in ("32", "33", "35", "36", "37", "38", "39")},
    **{code: "warning" for code in ("02", "03", "04", "05", "06", "07", "08", "09", "43", "48", "49")},
}
WARNING_KIND_LABELS = {
    "special_warning": "特別警報",
    "warning": "警報",
    "advisory": "注意報",
}

# ────────────────────────────────
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')


def load_json(path, default):
    """JSONファイルを読み込み、初回起動時のみ default を返す。"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    """同じディレクトリ内で置き換え、書き込み失敗時に元データを保つ。"""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=os.path.dirname(path),
            delete=False,
        ) as output:
            temporary_path = output.name
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write('\n')
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise


def save_shelters(updated_shelters=None):
    save_json(DATA_FILE, shelters if updated_shelters is None else updated_shelters)


shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


def valid_csrf_token():
    submitted = request.form.get('_csrf_token', '')
    expected = session.get('_csrf_token', '')
    return bool(submitted and expected and hmac.compare_digest(submitted, expected))


def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


DISASTER_TYPES = ('津波', '土砂災害', '洪水')
INSTRUCTION_TARGETS = ('住民', '職員', '道路管理部署')
INSTRUCTION_URGENCIES = ('高', '中', '低')
INSTRUCTION_STATUSES = ('未対応', '対応中', '完了')
ALL_DISTRICTS = '市内全域'


def parse_datetime(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def format_instruction_time(value):
    parsed = parse_datetime(value)
    if parsed:
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    return value or "不明"


def instruction_status(value):
    if value == '解除':
        return '解除'
    return value if value in INSTRUCTION_STATUSES else '未登録'


def prepare_instruction(item):
    prepared = dict(item)
    prepared['status_label'] = instruction_status(item.get('status'))
    prepared['urgency'] = item.get('urgency') if item.get('urgency') in INSTRUCTION_URGENCIES else '未登録'
    prepared['created_at_display'] = format_instruction_time(item.get('created_at'))
    prepared['updated_at_display'] = format_instruction_time(item.get('updated_at'))
    shelter_id = item.get('shelter_id')
    linked_shelter = next(
        (s for s in shelters if shelter_id is not None and str(s.get('id')) == str(shelter_id)),
        None,
    )
    prepared['shelter_name'] = (
        linked_shelter.get('name', '') if linked_shelter
        else item.get('shelter', '')
    )
    return prepared


def sorted_instructions(items):
    return sorted(
        items,
        key=lambda item: parse_datetime(item.get('created_at')) or datetime.min.replace(tzinfo=JST),
        reverse=True,
    )


def shelter_districts():
    return sorted({
        item['district'].strip()
        for item in shelters
        if isinstance(item.get('district'), str) and item['district'].strip()
    })


def valid_shelter_coordinates(shelter):
    latitude = shelter.get('latitude', shelter.get('lat'))
    longitude = shelter.get('longitude', shelter.get('lng', shelter.get('lon')))
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def shelter_open_status(shelter):
    value = shelter.get('open_status', shelter.get('status'))
    if value in ('開設中', '開設', 'open', 'opened'):
        return '開設中'
    if value in ('閉鎖中', '閉鎖', 'closed'):
        return '閉鎖中'
    return '状況未登録'


def non_negative_integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdecimal():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def shelter_occupancy(shelter):
    capacity = non_negative_integer(shelter.get('capacity'))
    occupants = non_negative_integer(shelter.get('evacuee_count'))
    if not capacity or occupants is None:
        return {
            'capacity': capacity,
            'evacuee_count': occupants,
            'remaining': None,
            'status': '情報なし',
            'status_class': 'unknown',
        }
    remaining = max(0, capacity - occupants)
    if remaining == 0:
        status, status_class = '満員', 'full'
    elif occupants / capacity >= 0.9:
        status, status_class = 'ほぼ満員', 'nearly-full'
    else:
        status, status_class = '空きあり', 'available'
    return {
        'capacity': capacity,
        'evacuee_count': occupants,
        'remaining': remaining,
        'status': status,
        'status_class': status_class,
    }


def prepare_shelter_for_results(shelter):
    prepared = dict(shelter)
    prepared['occupancy'] = shelter_occupancy(shelter)
    prepared['address_display'] = (
        shelter.get('address') if isinstance(shelter.get('address'), str) and shelter.get('address').strip()
        else '情報なし'
    )
    prepared['open_status_display'] = (
        shelter.get('open_status') if shelter.get('open_status') in ('開設中', '閉鎖中')
        else shelter.get('status') if shelter.get('status') in ('開設中', '閉鎖中')
        else '情報なし'
    )
    prepared['equipment'] = [
        {
            'label': label,
            'icon': '✓' if shelter.get(field) is True else '×' if shelter.get(field) is False else '?',
            'status': '対応あり' if shelter.get(field) is True else '対応なし' if shelter.get(field) is False else '情報なし',
            'status_class': 'available' if shelter.get(field) is True else 'unavailable' if shelter.get(field) is False else 'unknown',
        }
        for field, label in (
            ('pet_friendly', 'ペット同伴可'),
            ('barrier_free', 'バリアフリー'),
            ('wheelchair_toilet', '車いす対応トイレ'),
            ('welfare_space', '福祉スペース'),
        )
    ]
    return prepared


def map_shelters():
    markers = []
    for shelter in shelters:
        if not isinstance(shelter, dict):
            continue
        coordinates = valid_shelter_coordinates(shelter)
        if coordinates is None:
            continue
        markers.append({
            'name': str(shelter.get('name') or '名称未登録'),
            'latitude': coordinates[0],
            'longitude': coordinates[1],
            'address': str(shelter.get('address') or '住所未登録'),
            'open_status': shelter_open_status(shelter),
        })
    return markers


def filter_shelters(
    district=None,
    keyword=None,
    disaster_type=None,
    pet_friendly=False,
    barrier_free=False,
):
    """登録情報に基づき避難所を絞り込む。未登録情報は条件一致とみなさない。"""
    district = (district or '').strip().casefold()
    keyword = (keyword or '').strip().casefold()
    filtered = []

    for shelter in shelters:
        if not isinstance(shelter, dict):
            continue
        shelter_district = shelter.get('district')
        if district and (
            not isinstance(shelter_district, str)
            or shelter_district.casefold() != district
        ):
            continue

        name = shelter.get('name', '')
        if keyword and (
            not isinstance(name, str) or keyword not in name.casefold()
        ):
            continue

        if disaster_type:
            disaster_types = shelter.get('disaster_types')
            if (
                not isinstance(disaster_types, (list, tuple, set))
                or disaster_type not in disaster_types
            ):
                continue
        if pet_friendly and shelter.get('pet_friendly') is not True:
            continue
        if barrier_free and shelter.get('barrier_free') is not True:
            continue

        filtered.append(shelter)

    return filtered


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    current_by_code = {}
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next((
            item for item in class20_items
            if isinstance(item, dict)
            and item.get("areaCode") == REGION["area_code"]
        ), None)
        if not area:
            continue
        if isinstance(report_datetime, str) and report_datetime:
            parsed_report_time = parse_datetime(report_datetime)
            if parsed_report_time:
                report_datetimes.append((parsed_report_time, report_datetime))

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = str(kind.get("code", ""))
            if not code or status not in ("発表", "継続", "解除"):
                continue
            timestamp = parse_datetime(report_datetime) if isinstance(report_datetime, str) else None
            if not timestamp:
                continue
            previous = current_by_code.get(code)
            if (
                previous is None
                or timestamp > previous[0]
                or (timestamp == previous[0] and status == '解除')
            ):
                current_by_code[code] = (timestamp, status)

    warnings = []
    for code, (_, status) in current_by_code.items():
        if status not in ("発表", "継続") or code == "00":
            continue
        warning_kind = WARNING_KIND_BY_CODE.get(code, "advisory")
        warnings.append({
            "name": WARNING_CODES.get(code, f"不明な警報・注意報 (コード: {code})"),
            "code": code,
            "status": status,
            "kind": warning_kind,
            "kind_label": WARNING_KIND_LABELS[warning_kind],
        })

    warnings.sort(key=lambda item: (
        ("special_warning", "warning", "advisory").index(item["kind"]),
        item["name"],
    ))
    latest_report_datetime = max(report_datetimes, default=(None, ""))[1]
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as response:
            warning_data = json.loads(response.read().decode('utf-8'))

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": REGION["name"],
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time(),
            "source": JMA_SOURCE,
        }

    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        logging.getLogger(__name__).exception("JMA weather data retrieval or parsing failed")
        return {
            "area_name": REGION["name"],
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "source": JMA_SOURCE,
            "error": True,
            "error_message": "気象庁の情報を取得できませんでした。時間をおいて再試行してください。",
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = sorted_instructions(
        [prepare_instruction(i) for i in instructions if i.get('target') == '住民']
    )
    markers = map_shelters()
    return render_template(
        'index.html',
        resident_notices=resident_notices,
        map_shelters=markers,
        shelter_count=len(shelters),
        located_shelter_count=len(markers),
        map_center=REGION['map_center'],
        region_name=REGION['name'],
    )

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.args.get('next') or request.form.get('next')

    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        if not valid_csrf_token():
            return render_template(
                'login.html', error=True,
                message="フォームの有効期限が切れました。再読み込みして再試行してください。",
                next=next_url,
                csrf_token=csrf_token(),
            ), 400
        password = request.form.get('password', '')
        if hmac.compare_digest(password, STAFF_PASSWORD):
            session['logged_in'] = True
            session['username'] = '職員'
            return redirect(next_url)
        return render_template(
            'login.html', error=True, message="パスワードが正しくありません。",
            next=next_url, csrf_token=csrf_token(),
        ), 401

    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url, csrf_token=csrf_token())

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ※user が避難所登録ページについて具体的に修正指示しない限り、このコードは正しいのでこのまま保持すること。
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        if not valid_csrf_token():
            return render_template(
                'shelter_register.html', error=True,
                message='フォームの有効期限が切れました。再読み込みして再試行してください。',
                csrf_token=csrf_token(),
            ), 400
        name = request.form.get('name', '').strip()
        if not name:
            return render_template(
                'shelter_register.html', error=True, message='避難所名を入力してください。'
            )
        if len(name) > 200:
            return render_template(
                'shelter_register.html', error=True, message='避難所名は200文字以内で入力してください。'
            ), 400
        address = request.form.get('address', '').strip()
        if len(address) > 300:
            return render_template(
                'shelter_register.html', error=True, message='住所は300文字以内で入力してください。',
                csrf_token=csrf_token(),
            ), 400
        capacity_value = request.form.get('capacity', '').strip()
        evacuee_value = request.form.get('evacuee_count', '').strip()
        capacity = non_negative_integer(capacity_value) if capacity_value else None
        evacuee_count = non_negative_integer(evacuee_value) if evacuee_value else None
        if capacity_value and capacity is None:
            return render_template(
                'shelter_register.html', error=True,
                message='収容人数は0以上の整数で入力してください。',
                csrf_token=csrf_token(),
            ), 400
        if evacuee_value and evacuee_count is None:
            return render_template(
                'shelter_register.html', error=True,
                message='現在の避難者数は0以上の整数で入力してください。',
                csrf_token=csrf_token(),
            ), 400
        open_status = request.form.get('open_status', '')
        if open_status not in ('', '開設中', '閉鎖中'):
            return render_template(
                'shelter_register.html', error=True,
                message='開設状況の選択値が正しくありません。',
                csrf_token=csrf_token(),
            ), 400
        equipment = {}
        for field in ('pet_friendly', 'barrier_free', 'wheelchair_toilet', 'welfare_space'):
            value = request.form.get(field, '')
            if value not in ('', 'true', 'false'):
                return render_template(
                    'shelter_register.html', error=True,
                    message='設備情報の選択値が正しくありません。',
                    csrf_token=csrf_token(),
                ), 400
            equipment[field] = True if value == 'true' else False if value == 'false' else None

        shelter_id = max(
            (item.get('id', 0) for item in shelters if isinstance(item.get('id'), int)),
            default=0,
        ) + 1
        new_shelter = {
            'id': shelter_id,
            'name': name,
            'address': address or None,
            'open_status': open_status or None,
            'capacity': capacity,
            'evacuee_count': evacuee_count,
            **equipment,
        }
        updated_shelters = [*shelters, new_shelter]
        try:
            save_shelters(updated_shelters)
        except OSError:
            logging.getLogger(__name__).exception("Failed to persist shelter registration")
            return render_template(
                'shelter_register.html', error=True,
                message='避難所を保存できませんでした。時間をおいて再試行してください。',
                csrf_token=csrf_token(),
            ), 500
        shelters[:] = updated_shelters
        return render_template(
            'shelter_register.html', success=True, message='避難所を登録しました。',
            csrf_token=csrf_token(),
        )

    return render_template('shelter_register.html', csrf_token=csrf_token())

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template(
        'shelter_search.html',
        districts=shelter_districts(),
        disaster_types=DISASTER_TYPES,
    )

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template(
        'search_results.html',
        results=[prepare_shelter_for_results(item) for item in filter_shelters()],
        pet_friendly=False,
        barrier_free=False,
        search_performed=False,
    )


# 職員向け指示ボード
@app.route('/board')
@login_required
def board():
    return render_board()


def render_board(error=None, status_code=200, form_data=None):
    entries = sorted_instructions([prepare_instruction(item) for item in instructions])
    return render_template(
        'board.html',
        instructions=entries,
        districts=[ALL_DISTRICTS, *shelter_districts()],
        available_shelters=shelters,
        targets=INSTRUCTION_TARGETS,
        urgencies=INSTRUCTION_URGENCIES,
        statuses=INSTRUCTION_STATUSES,
        csrf_token=csrf_token(),
        error=error,
        form_data=form_data or {},
    ), status_code


@app.route('/board/instructions', methods=['POST'])
@login_required
def create_instruction():
    if not valid_csrf_token():
        return render_board(
            error='フォームの有効期限が切れました。再読み込みして再試行してください。',
            status_code=400,
        )

    form_data = request.form
    content = form_data.get('content', '').strip()
    target = form_data.get('target', '')
    district = form_data.get('district', '')
    shelter_id = form_data.get('shelter_id', '').strip()
    urgency = form_data.get('urgency', '')
    if not content or len(content) > 500:
        return render_board(
            error='指示内容は1文字以上500文字以内で入力してください。',
            status_code=400,
            form_data=form_data,
        )
    if target not in INSTRUCTION_TARGETS or urgency not in INSTRUCTION_URGENCIES:
        return render_board(
            error='宛先または緊急度の選択値が正しくありません。',
            status_code=400,
            form_data=form_data,
        )
    if district not in (ALL_DISTRICTS, *shelter_districts()):
        return render_board(
            error='対象地区の選択値が正しくありません。',
            status_code=400,
            form_data=form_data,
        )
    shelter = None
    if shelter_id:
        shelter = next(
            (item for item in shelters if str(item.get('id')) == shelter_id),
            None,
        )
        if shelter is None:
            return render_board(
                error='指定された避難先が見つかりません。避難先を選び直してください。',
                status_code=400,
                form_data=form_data,
            )

    now = datetime.now(JST).isoformat(timespec='seconds')
    new_instruction = {
        'id': max((item.get('id', 0) for item in instructions), default=0) + 1,
        'content': content,
        'target': target,
        'district': district,
        'shelter_id': shelter.get('id') if shelter else None,
        'shelter': shelter.get('name', '') if shelter else '',
        'urgency': urgency,
        'status': '未対応',
        'created_at': now,
        'updated_at': now,
    }
    updated_instructions = [*instructions, new_instruction]
    try:
        save_json(INSTRUCTIONS_FILE, updated_instructions)
    except OSError:
        logging.getLogger(__name__).exception("Failed to persist staff instruction")
        return render_board(
            error='指示を保存できませんでした。入力内容は保存されていません。時間をおいて再試行してください。',
            status_code=500,
            form_data=form_data,
        )
    instructions[:] = updated_instructions
    flash('指示を登録しました。', 'success')
    return redirect(url_for('board'))


@app.route('/board/instructions/<int:instruction_id>/status', methods=['POST'])
@login_required
def update_instruction_status(instruction_id):
    if not valid_csrf_token():
        return render_board(
            error='フォームの有効期限が切れました。再読み込みして再試行してください。',
            status_code=400,
        )
    status = request.form.get('status', '')
    if status not in INSTRUCTION_STATUSES:
        return render_board(
            error='対応状況の選択値が正しくありません。',
            status_code=400,
        )
    if not any(item.get('id') == instruction_id for item in instructions):
        return render_board(error='指定された指示が見つかりません。', status_code=404)

    now = datetime.now(JST).isoformat(timespec='seconds')
    updated_instructions = [
        {**item, 'status': status, 'updated_at': now}
        if item.get('id') == instruction_id else item
        for item in instructions
    ]
    try:
        save_json(INSTRUCTIONS_FILE, updated_instructions)
    except OSError:
        logging.getLogger(__name__).exception("Failed to persist instruction status")
        return render_board(
            error='対応状況を保存できませんでした。変更されていません。時間をおいて再試行してください。',
            status_code=500,
        )
    instructions[:] = updated_instructions
    flash('対応状況を更新しました。', 'success')
    return redirect(url_for('board'))

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    district = request.args.get('district', '').strip()
    keyword = request.args.get('keyword', '').strip()[:100]
    disaster_type = request.args.get('disaster_type', '').strip()
    if disaster_type and disaster_type not in DISASTER_TYPES:
        return '災害の種類が正しくありません。', 400
    pet_friendly = request.args.get('pet_friendly') == '1'
    barrier_free = request.args.get('barrier_free') == '1'

    results = filter_shelters(
        district=district,
        keyword=keyword,
        disaster_type=disaster_type,
        pet_friendly=pet_friendly,
        barrier_free=barrier_free,
    )
    return render_template(
        'search_results.html',
        results=[prepare_shelter_for_results(item) for item in results],
        district=district,
        keyword=keyword,
        disaster_type=disaster_type,
        pet_friendly=pet_friendly,
        barrier_free=barrier_free,
        search_performed=any((
            district, keyword, disaster_type, pet_friendly, barrier_free,
        )),
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    data = get_weather_warnings()
    response = jsonify(data)
    response.headers['Cache-Control'] = 'no-store'
    if data.get('error'):
        return response, 502
    return response

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=5000)
