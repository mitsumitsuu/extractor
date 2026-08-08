import streamlit as st
import pandas as pd
import re
import requests
import json
import urllib.parse
from googleapiclient.discovery import build
import google.generativeai as genai
from io import BytesIO
import yt_dlp
import streamlit.components.v1 as components
import sqlite3
import hashlib
import base64
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate
import uuid
import datetime

# ==========================================
# 0. メール送信設定
# ==========================================
SENDER_EMAIL = "yukimitsuyamamura0315@gmail.com"
SENDER_PASSWORD = "eyic edzf kved ewjg"

# ==========================================
# 1. データベース初期化
# ==========================================
def init_db():
    conn = sqlite3.connect('app_data.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, email TEXT, language TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS presets (username TEXT, preset_id INTEGER, data TEXT, PRIMARY KEY(username, preset_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (username TEXT PRIMARY KEY, hide_warning BOOLEAN)''')
    c.execute('''CREATE TABLE IF NOT EXISTS password_resets (token TEXT PRIMARY KEY, username TEXT, expiry DATETIME)''')
    conn.commit()
    return conn

conn = init_db()

def hash_password(password): return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password, email, language):
    try:
        conn.cursor().execute("INSERT INTO users (username, password, email, language) VALUES (?, ?, ?, ?)", 
                              (username, hash_password(password), email, language))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def login_user(username, password):
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, hash_password(password)))
    return c.fetchone()

def create_reset_token(username):
    token = str(uuid.uuid4())
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn.cursor().execute("INSERT INTO password_resets (token, username, expiry) VALUES (?, ?, ?)", (token, username, expiry))
    conn.commit()
    return token

def reset_password(token, new_password):
    c = conn.cursor()
    c.execute("SELECT username, expiry FROM password_resets WHERE token=?", (token,))
    row = c.fetchone()
    if row and datetime.datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S.%f') > datetime.datetime.now():
        username = row[0]
        c.execute("UPDATE users SET password=? WHERE username=?", (hash_password(new_password), username))
        c.execute("DELETE FROM password_resets WHERE token=?", (token,))
        conn.commit()
        return True
    return False

def send_reset_email(email, username, token, language, gemini_key):
    if not SENDER_EMAIL or "your_email" in SENDER_EMAIL: return False
    reset_link = f"https://your-app-url.com/?token={token}"
    base_text = f"パスワードの再設定リクエストを受け付けました。以下のリンクをクリックして新しいパスワードを設定してください。\n\nユーザー名: {username}\nリセットリンク: {reset_link}\n\n※このリンクは1時間有効です。"
    if gemini_key and language != "日本語":
        try:
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            body = model.generate_content(f"Translate the following email into {language}. Keep the links intact:\n{base_text}").text
        except: body = base_text
    else:
        body = base_text

    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = "Password Reset Request / パスワード再設定"
    msg['From'] = SENDER_EMAIL
    msg['To'] = email
    msg['Date'] = formatdate()

    try:
        smtp = smtplib.SMTP('smtp.gmail.com', 587)
        smtp.starttls()
        smtp.login(SENDER_EMAIL, SENDER_PASSWORD)
        smtp.sendmail(SENDER_EMAIL, email, msg.as_string())
        smtp.close()
        return True
    except Exception:
        return False

def save_preset_to_db(username, preset_id, data_dict):
    conn.cursor().execute("REPLACE INTO presets (username, preset_id, data) VALUES (?, ?, ?)", (username, preset_id, json.dumps(data_dict)))
    conn.commit()

def load_presets_from_db(username):
    c = conn.cursor()
    c.execute("SELECT preset_id, data FROM presets WHERE username=?", (username,))
    return {r[0]: json.loads(r[1]) for r in c.fetchall()}

def save_setting_to_db(username, hide_warning):
    conn.cursor().execute("REPLACE INTO settings (username, hide_warning) VALUES (?, ?)", (username, hide_warning))
    conn.commit()

def load_setting_from_db(username):
    c = conn.cursor()
    c.execute("SELECT hide_warning FROM settings WHERE username=?", (username,))
    row = c.fetchone()
    return bool(row[0]) if row else False

# ==========================================
# 2. UIカスタマイズ＆JavaScript
# ==========================================
st.set_page_config(page_title="楽曲抽出＆特定システム Ultimate", layout="wide", initial_sidebar_state="collapsed")

hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
.st-emotion-cache-1wbqy5l {display: none;}
.block-container { padding-top: 1rem !important; padding-bottom: 2rem !important; }
div[data-testid="stTabs"] > div:first-of-type {
    position: sticky; top: 0px; z-index: 999; background-color: #ffffff; padding-top: 10px; border-bottom: 1px solid #ddd;
}
div[data-baseweb="tab-panel"] { animation: none !important; transition: none !important; }
@media (max-width: 768px) { .desktop-only { display: none !important; } }
@media (prefers-color-scheme: dark) { div[data-testid="stTabs"] > div:first-of-type { background-color: #0e1117; border-bottom: 1px solid #333; } }
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

js_code = """
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script>
function googleTranslateElementInit() {
  new google.translate.TranslateElement({pageLanguage: 'ja', layout: google.translate.TranslateElement.InlineLayout.SIMPLE}, 'google_translate_element');
}
const doc = window.parent.document;
doc.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
        const active = doc.activeElement;
        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) {
            const inputs = Array.from(doc.querySelectorAll('input:not([type="hidden"]):not([disabled]), textarea:not([disabled])'));
            const index = inputs.indexOf(active);
            if (index > -1 && index < inputs.length - 1) { e.preventDefault(); inputs[index + 1].focus(); }
        }
    }
    const isCtrlShiftRight = e.ctrlKey && e.shiftKey && e.key === 'ArrowRight';
    const isCtrlShiftLeft = e.ctrlKey && e.shiftKey && e.key === 'ArrowLeft';
    if (isCtrlShiftRight || isCtrlShiftLeft) {
        e.preventDefault();
        const tabs = Array.from(doc.querySelectorAll('button[data-baseweb="tab"]'));
        let activeIdx = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
        if (activeIdx > -1) {
            let nextIdx = isCtrlShiftLeft ? (activeIdx - 1 + tabs.length) % tabs.length : (activeIdx + 1) % tabs.length;
            tabs[nextIdx].click();
        }
    }
    if (e.ctrlKey && e.shiftKey && (e.key === 'r' || e.key === 'R')) {
        e.preventDefault();
        const activeTabPanel = doc.querySelector('div[data-baseweb="tab-panel"][aria-hidden="false"]');
        if (activeTabPanel) {
            const resetBtn = Array.from(activeTabPanel.querySelectorAll('button')).find(b => b.innerText.includes('🔄 プリセット'));
            if (resetBtn) resetBtn.click();
        }
    }
});
setInterval(() => {
    doc.querySelectorAll('input').forEach(el => {
        el.setAttribute('autocomplete', 'new-password');
        if(!el.hasAttribute('data-randomized')) { el.setAttribute('name', Math.random().toString(36).substring(7)); el.setAttribute('data-randomized', 'true'); }
    });
}, 1000);
</script>
<script type="text/javascript" src="//translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
"""
components.html(js_code, height=0, width=0)

# ==========================================
# 3. 初期設定とセッション管理
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花, GUMI, 音街ウナ"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"

if "first_visit" not in st.session_state: st.session_state.first_visit = True
if "logged_in_user" not in st.session_state: st.session_state.logged_in_user = None
if "hide_warning_forever" not in st.session_state: st.session_state.hide_warning_forever = False

def get_default_preset():
    return {
        "mode": "⚡ 高速モード (yt-dlp使用 / API不要)", "title_mode": "✨ スッキリ出力",
        "yt_key": "", "gemini_key": "", "url": "", "exclude_words": "", "target_vocal": "", "target_producer": "",
        "multi_only": False, "min_v": 0, "max_v": 0, "min_c": 0, "max_c": 0,
        "add_lyrics": True, "add_analysis": False, "add_bpm": True, "filename": "playlist"
    }

if "presets" not in st.session_state: st.session_state.presets = {i: get_default_preset() for i in range(1, 11)}
if "results" not in st.session_state: st.session_state.results = {i: None for i in range(1, 11)}

query_params = st.query_params
if "token" in query_params:
    st.subheader("🔐 パスワードの再設定")
    new_pass = st.text_input("新しいパスワードを入力", type="password")
    if st.button("パスワードを更新"):
        if reset_password(query_params["token"], new_pass):
            st.success("パスワードを更新しました。トップページに戻ってログインしてください。")
        else:
            st.error("トークンが無効または期限切れです。")
    st.stop()

# ==========================================
# 4. ヘッダー
# ==========================================
col_title, col_trans, col_auth = st.columns([5, 3, 2])
with col_title:
    st.title("🎶 楽曲抽出システム Ultimate")
with col_trans:
    st.markdown("<div style='margin-top: 15px;' id='google_translate_element'></div>", unsafe_allow_html=True)
with col_auth:
    st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
    with st.popover("⚙️ 設定 / 寄付 / アカウント"):
        st.markdown("**☕ 開発者を支援する**")
        st.markdown("[BuyMeACoffeeで寄付](https://www.buymeacoffee.com/) | [Ko-fiで寄付](https://ko-fi.com/)")
        st.markdown("---")
        if st.session_state.logged_in_user:
            st.success(f"👤 {st.session_state.logged_in_user}")
            if st.button("ログアウト", use_container_width=True):
                st.session_state.logged_in_user = None
                st.session_state.presets = {i: get_default_preset() for i in range(1, 11)}
                st.rerun()
            st.markdown("---")
            current_hide_setting = load_setting_from_db(st.session_state.logged_in_user)
            new_hide_setting = st.checkbox("初期化時の警告を隠す", value=current_hide_setting)
            if current_hide_setting != new_hide_setting:
                save_setting_to_db(st.session_state.logged_in_user, new_hide_setting)
                st.session_state.hide_warning_forever = new_hide_setting
                st.rerun()
            if st.button("💾 全プリセットを保存", use_container_width=True):
                for pid, p_data in st.session_state.presets.items(): save_preset_to_db(st.session_state.logged_in_user, pid, p_data)
                st.success("保存完了！")
        else:
            log_mode = st.radio("メニュー", ["ログイン", "新規登録", "パスワード忘却"], horizontal=True)
            if log_mode == "パスワード忘却":
                reset_user = st.text_input("ユーザー名")
                reset_email = st.text_input("登録したメールアドレス")
                reset_gemini = st.text_input("Gemini APIキー (多言語翻訳用/任意)", type="password")
                if st.button("リセットメールを送信"):
                    c = conn.cursor()
                    c.execute("SELECT language FROM users WHERE username=? AND email=?", (reset_user, reset_email))
                    user_data = c.fetchone()
                    if user_data:
                        token = create_reset_token(reset_user)
                        if send_reset_email(reset_email, reset_user, token, user_data[0], reset_gemini):
                            st.success("再設定リンクを送信しました。")
                        else:
                            st.error("メール送信設定がサーバー側にありません。管理者に連絡してください。")
                    else:
                        st.error("ユーザー名かメールアドレスが違います。")
            else:
                u_name = st.text_input("ユーザー名")
                u_pass = st.text_input("パスワード", type="password")
                if log_mode == "新規登録":
                    u_email = st.text_input("メールアドレス (パスワード再設定用)")
                    u_lang = st.selectbox("システム通知言語", ["日本語", "English", "Español", "中文", "한국어"])
                    if st.button("登録", use_container_width=True):
                        if register_user(u_name, u_pass, u_email, u_lang): st.success("登録完了！")
                        else: st.error("既に使用されています。")
                else:
                    if st.button("ログイン", use_container_width=True):
                        user_info = login_user(u_name, u_pass)
                        if user_info:
                            st.session_state.logged_in_user = u_name
                            try:
                                loaded = load_presets_from_db(u_name)
                                for pid, p_data in loaded.items(): st.session_state.presets[pid].update(p_data)
                            except: pass
                            st.rerun()
                        else: st.error("情報が違います。")

# ==========================================
# 5. データ処理関数
# ==========================================
def parse_flexible_input(text):
    if not text: return []
    return [w.strip() for w in re.split(r'[,\n\s、]+', text) if w.strip()]

def search_youtube_by_title(title):
    ydl_opts = {'extract_flat': True, 'quiet': True, 'ignoreerrors': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{title}", download=False)
            if 'entries' in info and len(info['entries']) > 0:
                entry = info['entries'][0]
                return f"https://www.youtube.com/watch?v={entry.get('id')}", entry.get('title', title), entry.get('view_count', 0)
    except: pass
    return "", title, 0

def extract_from_pasted_text(api_key, text_data):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    以下のテキスト(ランキングなど)から、「楽曲の題名」と「アーティスト名(または合成音声名)」を抽出してJSON配列で出力してください。
    フォーマット: [{{"title": "曲名", "artist": "アーティスト名"}}]
    【テキスト】
    {text_data}
    """
    try:
        response = model.generate_content(prompt)
        res_text = re.sub(r'`{3}(json)?', '', response.text, flags=re.IGNORECASE).strip()
        return json.loads(res_text[res_text.find('['):res_text.rfind(']')+1])
    except: return []

def clean_title(raw_title):
    title = str(raw_title)
    title = re.split(r'\s*[/／]\s*', title)[0]
    title = re.sub(r'\s+[^\s]*P\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r"(?i)[\(（\[【].*?(remix|bootleg|edit|mashup|flip|vip|cover|feat\.|long ver|short ver|MV|PV).*?[\)）\]】]", "", title)
    title = re.sub(r"【.*?】|\[.*?\]", "", title)
    title = re.split(r"(?i)\s+feat\.\s+|\s+ft\.\s+", title)[0]
    return title.strip()

def extract_vocals_manual(title, description, keywords, ng_list):
    found = set()
    title_str = str(title) if title else ""
    desc_str = str(description) if description else ""
    for kw in keywords:
        if kw in title_str: found.add(kw)
    if not any(ng in desc_str for ng in ng_list):
        for kw in keywords:
            if kw in desc_str: found.add(kw)
    return " / ".join(list(found))

def extract_vocals_ai(api_key, text_data):
    if not api_key: return "", ""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    以下の動画タイトルと概要欄から純粋な「楽曲の題名」と「歌唱している合成音声名」を抽出。余計な文字は排除。ボーカル複数は「/」区切り。JSONのみ出力。
    {{"title": "純粋な曲名", "vocals": "合成音声名"}}
    【データ】
    {text_data}
    """
    try:
        response = model.generate_content(prompt)
        res_text = re.sub(r'`{3}(json)?', '', response.text, flags=re.IGNORECASE).strip()
        start = res_text.find('{')
        end = res_text.rfind('}')
        if start != -1 and end != -1: res_text = res_text[start:end+1]
        result = json.loads(res_text)
        return result.get("title", ""), result.get("vocals", "")
    except Exception:
        return "", ""

def get_youtube_playlist_api(api_key, url, min_v, max_v, min_c, max_c):
    if not api_key: raise ValueError("YouTube APIキーが設定されていません。")
    match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
    if not match: raise ValueError("有効なYouTubeプレイリストIDが見つかりません。")
    youtube = build("youtube", "v3", developerKey=api_key)
    videos, next_page_token = [], None
    while True:
        request = youtube.playlistItems().list(part="snippet", playlistId=match.group(1), maxResults=50, pageToken=next_page_token)
        response = request.execute()
        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in response.get("items", []) if item["snippet"]["title"] not in ["Private video", "Deleted video"]]
        if not video_ids: break
        stats_req = youtube.videos().list(part="statistics", id=",".join(video_ids))
        stats_dict = {i["id"]: i["statistics"] for i in stats_req.execute().get("items", [])}
        for item in response.get("items", []):
            vid = item["snippet"]["resourceId"]["videoId"]
            title = item["snippet"]["title"]
            if title in ["Private video", "Deleted video"]: continue
            stats = stats_dict.get(vid, {})
            views = int(stats.get("viewCount", 0))
            comments = int(stats.get("commentCount", 0))
            if min_v > 0 and views < min_v: continue
            if max_v > 0 and views > max_v: continue
            if min_c > 0 and comments < min_c: continue
            if max_c > 0 and comments > max_c: continue
            videos.append({"曲名": title, "概要欄データ": item["snippet"].get("description", ""), "URL": f"https://www.youtube.com/watch?v={vid}"})
        next_page_token = response.get("nextPageToken")
        if not next_page_token: break
    return videos

def get_playlist_ytdlp(url):
    ydl_opts = {'extract_flat': True, 'quiet': True, 'ignoreerrors': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            videos = []
            for entry in info.get('entries', [info]):
                if entry and (entry.get('url') or entry.get('id')):
                    vid_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    videos.append({"曲名": entry.get('title', 'Unknown'), "概要欄データ": entry.get('description', ''), "URL": vid_url})
            return videos
    except Exception as e:
        raise ValueError(f"解析失敗: {e}")

def make_hyperlink(url, text):
    formula = f'=HYPERLINK("{url}", "{text}")'
    return formula if len(formula) <= 255 else url

def create_advanced_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter', engine_kwargs={'options': {'strings_to_urls': False}}) as writer:
        df.to_excel(writer, sheet_name='一括データ', index=False)
        if "合成音声" in df.columns:
            unique_vocals = set()
            for v in df['合成音声'].dropna():
                for part in str(v).split('/'):
                    if part.strip() and part.strip() != "手動抽出モード": unique_vocals.add(part.strip())
            for vocal in unique_vocals:
                safe_vocal = re.sub(r'[\\/*?:\[\]]', '', vocal)[:31]
                sub_df = df[df['合成音声'].astype(str).str.contains(vocal, na=False, regex=False)]
                if not sub_df.empty and safe_vocal.strip():
                    sub_df.to_excel(writer, sheet_name=safe_vocal, index=False)
            multi_df = df[df['合成音声'].astype(str).str.contains('/', na=False)]
            if not multi_df.empty:
                multi_df.to_excel(writer, sheet_name='複数人歌唱', index=False)
            vocs_order = ["初音ミク", "鏡音リン", "鏡音レン", "MEIKO", "KAITO", "GUMI", "音街ウナ", "可不", "星界", "重音テト"]
            vocs_order += [v for v in unique_vocals if v not in vocs_order]
            ws = writer.book.add_worksheet('ボーカル横並び配置')
            header_fmt = writer.book.add_format({'bold': True, 'bg_color': '#D3D3D3'})
            col_offset = 0
            for vocal in vocs_order:
                sub_df = df[df['合成音声'].astype(str).str.contains(vocal, na=False, regex=False)]
                if sub_df.empty: continue
                ws.write(0, col_offset, f"【{vocal}】", header_fmt)
                for c_idx, col_name in enumerate(sub_df.columns): ws.write(1, col_offset + c_idx, col_name, header_fmt)
                for r_idx, row in enumerate(sub_df.values):
                    for c_idx, val in enumerate(row): ws.write(r_idx + 2, col_offset + c_idx, str(val))
                col_offset += len(sub_df.columns) + 5
        for worksheet in writer.book.worksheets():
            try: worksheet.autofit()
            except: pass
    return output.getvalue()

# ==========================================
# 6. ガイド・お問い合わせ
# ==========================================
with st.expander("📖 詳しい使い方とショートカットキー / 🔑 API取得方法 / ✉️ お問い合わせ", expanded=True):
    col_guide, col_contact = st.columns([1, 1])
    with col_guide:
        st.markdown("""
        <div class="desktop-only">
        **【ショートカットキー (PC)】**
        *   `Ctrl`+`Shift`+`▶` : 右のプリセットへ / `Ctrl`+`Shift`+`◀` : 左へ
        *   `Ctrl`+`Shift`+`R` : 現在のプリセット初期化
        *   `Enter` : 次の入力項目へ移動
        </div>
        
        **【🔑 APIキーの取得方法】**
        *   **YouTube API Key:** [Google Cloud Console](https://console.cloud.google.com/) にアクセス ➔ プロジェクト作成 ➔ 「APIとサービス」から「YouTube Data API v3」を有効化 ➔ 「認証情報」からAPIキーを作成。
        *   **Gemini API Key:** [Google AI Studio](https://aistudio.google.com/) にアクセス ➔ 「Get API key」をクリック ➔ APIキーを作成。
        
        **【新機能: コピペ解析 (AI)】**
        BillboardやSNSのランキング文字をそのまま貼り付けて抽出開始すると、AIが曲名を認識し自動でYouTube動画を探し出してプレイリスト化します。
        """, unsafe_allow_html=True)
    with col_contact:
        with st.form("contact_form_top"):
            subject_input = st.text_input("件名", placeholder="バグ報告・要望")
            body_input = st.text_area("内容", height=100)
            if st.form_submit_button("管理者に送信"):
                try: requests.post("https://formsubmit.co/ajax/yukimitsuyamamura0315@gmail.com", data={"件名": subject_input, "メッセージ": body_input})
                except: pass

st.session_state.first_visit = False

# ==========================================
# 7. プリセットタブ
# ==========================================
preset_tabs = st.tabs([f"プリセット {i}" for i in range(1, 11)] + ["📁 プレイリスト作成＆照合"])

def trigger_reset_preset(pid):
    st.session_state.presets[pid] = get_default_preset()
    st.session_state.results[pid] = None
    if st.session_state.logged_in_user: save_preset_to_db(st.session_state.logged_in_user, pid, st.session_state.presets[pid])

for i in range(10):
    pid = i + 1
    with preset_tabs[i]:
        p = st.session_state.presets[pid]
        
        col_btn1, col_btn2 = st.columns([2, 7])
        with col_btn1:
            if st.button(f"🔄 プリセット{pid}を初期化", key=f"req_reset_{pid}"):
                if st.session_state.hide_warning_forever:
                    trigger_reset_preset(pid)
                    st.rerun()
                else:
                    st.session_state[f"show_warn_{pid}"] = True
        
        if st.session_state.get(f"show_warn_{pid}", False):
            st.warning("⚠️ 本当に初期化しますか？")
            cw1, cw2, cw3 = st.columns([2,2,6])
            with cw1:
                if st.button("はい、初期化する", key=f"yes_reset_{pid}"):
                    trigger_reset_preset(pid)
                    st.session_state[f"show_warn_{pid}"] = False
                    st.rerun()
            with cw2:
                if st.button("キャンセル", key=f"cancel_reset_{pid}"):
                    st.session_state[f"show_warn_{pid}"] = False
                    st.rerun()
            with cw3:
                if st.checkbox("次回からこの警告を表示しない", key=f"check_warn_{pid}"):
                    st.session_state.hide_warning_forever = True
                    if st.session_state.logged_in_user: save_setting_to_db(st.session_state.logged_in_user, True)

        st.markdown("### 🔍 解析元データの選択")
        data_source = st.radio("どの方法でデータを集めますか？", 
            ["🔗 YouTube/SoundCloudのURLから抽出", "📝 ランキングテキスト等をコピペしてAIに探させる", "📂 CSV/ExcelファイルをアップロードしてURLを補完する"],
            key=f"ds_{pid}"
        )
        
        if "URLから抽出" in data_source:
            p["url"] = st.text_area("🔗 URLを入力", value=p["url"], height=68, key=f"url_{pid}")
        elif "コピペ" in data_source:
            p["pasted_text"] = st.text_area("📋 BillboardやX(Twitter)のランキングテキストをそのまま貼り付け", height=150, key=f"paste_{pid}")
        else:
            p["upload_file"] = st.file_uploader("📂 楽曲名の入ったファイルをアップロード", type=["csv", "xlsx"], key=f"up_{pid}")

        st.markdown("---")
        with st.expander("⚙️ 抽出条件・詳細フィルター", expanded=True):
            p["mode"] = st.radio("抽出モード", ["⚡ 高速モード", "✨ AI完璧抽出モード (Gemini必須)"], horizontal=True, key=f"m_{pid}")
            c1, c2 = st.columns(2)
            with c1: p["yt_key"] = st.text_input("YouTube API Key", type="password", key=f"yk_{pid}")
            with c2: p["gemini_key"] = st.text_input("Gemini API Key", type="password", key=f"gk_{pid}")
            
            cv1, cv2 = st.columns(2)
            with cv1:
                p["min_v"] = st.number_input("最小再生数", value=p["min_v"], step=10000, key=f"minv_{pid}")
                p["max_v"] = st.number_input("最大再生数 (0で無制限)", value=p["max_v"], step=10000, key=f"maxv_{pid}")
            with cv2:
                p["min_c"] = st.number_input("最小コメント数", value=p["min_c"], step=100, key=f"minc_{pid}")
                p["max_c"] = st.number_input("最大コメント数 (0で無制限)", value=p["max_c"], step=100, key=f"maxc_{pid}")
            
            p["exclude_words"] = st.text_input("❌ 除外ワード", value=p.get("exclude_words", ""), key=f"ex_{pid}")
            p["target_vocal"] = st.text_input("🎯 この合成音声の曲だけ抽出", value=p["target_vocal"], placeholder="例: 初音ミク, 鏡音リン", key=f"tv_{pid}")
            p["target_producer"] = st.text_input("👤 このボカロPの曲だけ抽出", value=p.get("target_producer", ""), placeholder="例: DECO*27, ピノキオピー", key=f"tp_{pid}")
            p["multi_only"] = st.checkbox("👥 複数人が歌唱している曲のみ抽出する", value=p["multi_only"], key=f"mo_{pid}")
            
            match_file = st.file_uploader("📂 外部データ照合 (ランキングやセトリCSV等をアップロードして一致する曲のみ抽出)", type=["csv", "xlsx"], key=f"mf_{pid}")
            match_titles = set()
            if match_file:
                try:
                    match_df = pd.read_csv(match_file) if match_file.name.endswith('.csv') else pd.read_excel(match_file)
                    match_titles = set(match_df.astype(str).apply(lambda x: ' '.join(x), axis=1).str.cat(sep=' '))
                except Exception: pass
            
            cl1, cl2, cl3 = st.columns(3)
            with cl1: p["add_lyrics"] = st.checkbox("📝 歌詞サイトリンク", value=p["add_lyrics"], key=f"al_{pid}")
            with cl2: p["add_analysis"] = st.checkbox("🤔 考察/Wikiリンク", value=p["add_analysis"], key=f"aa_{pid}")
            with cl3: p["add_bpm"] = st.checkbox("🎛️ Tunebat BPM/Keyリンク", value=p["add_bpm"], key=f"ab_{pid}")
            
            p["filename"] = st.text_input("📄 出力ファイル名 (任意)", value=p.get("filename", "playlist"), key=f"fn_{pid}")

        col_exec, col_pl = st.columns([1, 1])
        with col_exec:
            if st.button("🚀 抽出開始", type="primary", use_container_width=True, key=f"btn_{pid}"):
                if "URLから抽出" in data_source and not p["url"].strip():
                    st.warning("URLを入力してください。")
                else:
                    with st.spinner(f"プリセット {pid} で解析を実行中..."):
                        try:
                            ex_list = parse_flexible_input(p["exclude_words"])
                            tv_list = parse_flexible_input(p["target_vocal"])
                            tp_list = parse_flexible_input(p.get("target_producer", ""))
                            kw_list = [k.strip() for k in DEFAULT_KEYWORDS.split(',')]
                            ng_list = [n.strip() for n in DEFAULT_NG_WORDS.split(',')]
                            
                            results = []
                            
                            if "コピペ" in data_source and p.get("pasted_text"):
                                if not p["gemini_key"]: st.error("AI機能を使用するにはGemini APIキーが必要です。")
                                else:
                                    extracted_list = extract_from_pasted_text(p["gemini_key"], p["pasted_text"])
                                    progress_bar = st.progress(0)
                                    for idx, item in enumerate(extracted_list):
                                        title_query = f"{item['title']} {item['artist']}"
                                        vid_url, yt_title, _ = search_youtube_by_title(title_query)
                                        if vid_url:
                                            results.append({
                                                "曲名": clean_title(item['title']), "合成音声": item['artist'], 
                                                "URL": make_hyperlink(vid_url, vid_url)
                                            })
                                        progress_bar.progress((idx + 1) / len(extracted_list))
                                        
                            elif "URLから抽出" in data_source and p["url"]:
                                raw_data = []
                                if "統計" in p["mode"]: raw_data = get_youtube_playlist_api(p["yt_key"], p["url"].strip(), p["min_v"], p["max_v"], p["min_c"], p["max_c"])
                                else: raw_data = get_playlist_ytdlp(p["url"].strip())
                                
                                for item in raw_data:
                                    raw_t = item["曲名"]
                                    desc = item["概要欄データ"]
                                    url = item["URL"]
                                    
                                    if any(ex in raw_t for ex in ex_list): continue
                                    if tp_list and not any(tp in raw_t or tp in desc for tp in tp_list): continue
                                        
                                    if "AI" in p["mode"] and p["gemini_key"]:
                                        clean_t, vocals = extract_vocals_ai(p["gemini_key"], f"{raw_t}\n{desc}")
                                        if not clean_t: clean_t = clean_title(raw_t) if "スッキリ" in p["title_mode"] else raw_t
                                    else:
                                        clean_t = clean_title(raw_t) if "スッキリ" in p["title_mode"] else raw_t
                                        vocals = extract_vocals_manual(raw_t, desc, kw_list, ng_list)
                                    
                                    if match_file and clean_t not in match_titles: continue
                                    if tv_list and not any(tv in vocals for tv in tv_list): continue
                                    if p["multi_only"] and "/" not in vocals: continue
                                    
                                    safe_t = str(clean_t) if clean_t else "Unknown"
                                    encoded = urllib.parse.quote(safe_t)
                                    
                                    row = {"曲名": clean_t, "合成音声": vocals, "URL": make_hyperlink(url, url)}
                                    if p["add_lyrics"]: row["歌詞検索"] = make_hyperlink(f"https://www.uta-net.com/search/?keyword={encoded}", "Uta-Netで歌詞を見る")
                                    if p["add_analysis"]: row["初音ミクwiki検索"] = make_hyperlink(f"https://w.atwiki.jp/hmiku/search?andor=and&keyword={encoded}", "初音ミクwikiで見る")
                                    if p["add_bpm"]: row["BPM・キー検索"] = make_hyperlink(f"https://tunebat.com/Search?q={encoded}", "Tunebatで検索")
                                        
                                    results.append(row)
                                    
                            elif "ファイル" in data_source and p.get("upload_file"):
                                try:
                                    up_df = pd.read_csv(p["upload_file"]) if p["upload_file"].name.endswith('.csv') else pd.read_excel(p["upload_file"])
                                    progress_bar = st.progress(0)
                                    for idx, row in up_df.iterrows():
                                        query = " ".join([str(v) for v in row.values if str(v) != 'nan'])
                                        vid_url, yt_title, _ = search_youtube_by_title(query)
                                        row_dict = row.to_dict()
                                        row_dict["YouTube_URL"] = make_hyperlink(vid_url, vid_url) if vid_url else "見つかりませんでした"
                                        results.append(row_dict)
                                        progress_bar.progress((idx + 1) / len(up_df))
                                except Exception as e:
                                    st.error(f"ファイル読み込みエラー: {e}")

                            if results:
                                st.session_state.results[pid] = pd.DataFrame(results)
                                if st.session_state.logged_in_user: save_preset_to_db(st.session_state.logged_in_user, pid, p)
                            else:
                                st.warning("条件に一致する楽曲がありませんでした。")
                                st.session_state.results[pid] = None

                        except Exception as e:
                            st.error(f"エラーが発生しました: {e}")
                            st.session_state.results[pid] = None

        with col_pl:
            saved_df = st.session_state.results[pid]
            if saved_df is not None and not saved_df.empty:
                if st.button("🎧 抽出結果からプレイリストを作成", use_container_width=True, key=f"pl_{pid}"):
                    video_ids = []
                    for url_val in saved_df.get("URL", saved_df.get("YouTube_URL", [])):
                        match = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([a-zA-Z0-9_-]{11})", str(url_val))
                        if match: video_ids.append(match.group(1))
                    
                    if video_ids:
                        chunked_ids = [video_ids[i:i + 50] for i in range(0, len(video_ids), 50)]
                        for idx, chunk in enumerate(chunked_ids):
                            playlist_url = f"https://www.youtube.com/watch_videos?video_ids={','.join(chunk)}"
                            st.info(f"**Part {idx+1}**: {playlist_url}")
                    else: st.error("有効なリンクがありません。")

        if saved_df is not None and not saved_df.empty:
            st.success(f"✅ {len(saved_df)}曲抽出")
            c_dl1, c_dl2, c_dl3 = st.columns(3)
            fname = p.get("filename", "playlist")
            with c_dl1:
                excel_data = create_advanced_excel(saved_df)
                st.download_button("📥 XLSXを保存", excel_data, f"{fname}.xlsx", key=f"dl_{pid}")
            with c_dl2:
                csv_df = saved_df.copy()
                for col in csv_df.columns:
                    if csv_df[col].dtype == object: csv_df[col] = csv_df[col].apply(lambda x: re.search(r'"(https?://.*?)"', str(x)).group(1) if '=HYPERLINK' in str(x) else x)
                csv = csv_df.to_csv(index=False).encode('utf-8-sig')
                st.download_button("📥 CSVダウンロード", csv, f"{fname}.csv", "text/csv")
            with c_dl3:
                txt_data = csv_df.to_csv(index=False, sep='\t').encode('utf-8')
                st.download_button("📥 TXTダウンロード", txt_data, f"{fname}.txt", "text/plain")
            
            b64_csv = base64.b64encode(csv_df.to_csv(index=False, sep='\t').encode('utf-8')).decode('utf-8')
            copy_html = f"""
            <button id="copyBtn{pid}" onclick="copyData{pid}()" style="padding: 10px 20px; background-color: #2e7d32; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; font-size: 14px; margin-bottom: 5px;">
                📋 表データをクリップボードにコピー
            </button>
            <button id="imgBtn{pid}" onclick="downloadImage{pid}()" style="padding: 10px 20px; background-color: #1565c0; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; font-size: 14px; margin-bottom: 5px; margin-left: 10px;">
                🖼️ 表を画像(PNG)で保存
            </button>
            <script>
            function copyData{pid}() {{
                const btn = document.getElementById("copyBtn{pid}");
                const str = decodeURIComponent(escape(window.atob('{b64_csv}')));
                navigator.clipboard.writeText(str).then(function() {{
                    btn.innerHTML = "✅ コピーしました！"; btn.style.backgroundColor = "#1b5e20";
                    setTimeout(function() {{ btn.innerHTML = "📋 表データをクリップボードにコピー"; btn.style.backgroundColor = "#2e7d32"; }}, 2000);
                }});
            }}
            function downloadImage{pid}() {{
                const table = window.parent.document.querySelector('div[data-testid="stDataFrame"]');
                if(table) {{
                    html2canvas(table).then(canvas => {{
                        const link = document.createElement('a');
                        link.download = '{fname}.png';
                        link.href = canvas.toDataURL();
                        link.click();
                    }});
                }}
            }}
            </script>
            """
            components.html(copy_html, height=50)
            
            total_height = (len(saved_df) * 35) + 40
            st.dataframe(saved_df, height=total_height, use_container_width=True)

with preset_tabs[10]:
    st.header("📁 プレイリスト作成 ＆ URL結合")
    uploaded_pl_file = st.file_uploader("URLが含まれた楽曲リスト (Excel/CSV) をアップロード", type=["xlsx", "csv"])
    if st.button("プレイリストURLを生成する", type="primary"):
        if uploaded_pl_file is not None:
            try:
                pl_df = pd.read_csv(uploaded_pl_file) if uploaded_pl_file.name.endswith('.csv') else pd.read_excel(uploaded_pl_file)
                video_ids = []
                for idx, row in pl_df.iterrows():
                    for item in row.values:
                        match = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([a-zA-Z0-9_-]{11})", str(item))
                        if match: video_ids.append(match.group(1)); break
                if video_ids:
                    chunked_ids = [video_ids[i:i + 50] for i in range(0, len(video_ids), 50)]
                    for idx, chunk in enumerate(chunked_ids):
                        playlist_url = f"https://www.youtube.com/watch_videos?video_ids={','.join(chunk)}"
                        st.markdown(f"**🎧 プレイリスト Part {idx+1} (最大50曲):**\n[ここをクリックして連続再生を開始する]({playlist_url})")
                        st.code(playlist_url)
                else:
                    st.error("有効なYouTube動画リンクが見つかりませんでした。")
            except Exception as e:
                st.error(f"ファイル読み込みエラー: {e}")
        else:
            st.warning("ファイルをアップロードしてください。")
