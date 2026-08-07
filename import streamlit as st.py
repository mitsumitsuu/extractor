import streamlit as st
import pandas as pd
import re
import requests
import json
import urllib.parse
import urllib.request
from googleapiclient.discovery import build
import google.generativeai as genai
from io import BytesIO
import yt_dlp
import streamlit.components.v1 as components
import sqlite3
import hashlib
import base64

# ==========================================
# 0. データベース初期化とログイン機能
# ==========================================
def init_db():
    conn = sqlite3.connect('app_data.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS presets (username TEXT, preset_id INTEGER, data TEXT, PRIMARY KEY(username, preset_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (username TEXT PRIMARY KEY, hide_warning BOOLEAN)''')
    conn.commit()
    return conn

conn = init_db()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password):
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hash_password(password)))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def login_user(username, password):
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, hash_password(password)))
    return c.fetchone() is not None

def save_preset_to_db(username, preset_id, data_dict):
    c = conn.cursor()
    data_str = json.dumps(data_dict)
    c.execute("REPLACE INTO presets (username, preset_id, data) VALUES (?, ?, ?)", (username, preset_id, data_str))
    conn.commit()

def load_presets_from_db(username):
    c = conn.cursor()
    c.execute("SELECT preset_id, data FROM presets WHERE username=?", (username,))
    rows = c.fetchall()
    loaded = {}
    for r in rows:
        loaded[r[0]] = json.loads(r[1])
    return loaded

def save_setting_to_db(username, hide_warning):
    c = conn.cursor()
    c.execute("REPLACE INTO settings (username, hide_warning) VALUES (?, ?)", (username, hide_warning))
    conn.commit()

def load_setting_from_db(username):
    c = conn.cursor()
    c.execute("SELECT hide_warning FROM settings WHERE username=?", (username,))
    row = c.fetchone()
    return bool(row[0]) if row else False

# ==========================================
# 1. UIカスタマイズ＆JavaScript強制注入
# ==========================================
st.set_page_config(page_title="楽曲抽出＆特定システム Ultimate", layout="wide", initial_sidebar_state="collapsed")

# 謎の空白削減 ＆ プリセット名（タブ）の追従（Sticky）設定
hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.st-emotion-cache-1wbqy5l {display: none;}
.block-container {
    padding-top: 1rem !important;
    padding-bottom: 2rem !important;
}
/* タブの追従 (Sticky) */
div[data-testid="stTabs"] > div:first-of-type {
    position: sticky;
    top: 0px;
    z-index: 999;
    background-color: #ffffff;
    padding-top: 10px;
    padding-bottom: 5px;
    border-bottom: 1px solid #ddd;
}
@media (prefers-color-scheme: dark) {
    div[data-testid="stTabs"] > div:first-of-type {
        background-color: #0e1117;
        border-bottom: 1px solid #333;
    }
}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

js_code = """
<script>
const doc = window.parent.document;
doc.addEventListener('keydown', function(e) {
    // 1. Enterキーでのフォーカス移動
    if (e.key === 'Enter') {
        const active = doc.activeElement;
        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) {
            const inputs = Array.from(doc.querySelectorAll('input:not([type="hidden"]):not([disabled]), textarea:not([disabled])'));
            const index = inputs.indexOf(active);
            if (index > -1 && index < inputs.length - 1) {
                e.preventDefault();
                inputs[index + 1].focus();
            }
        }
    }
    
    // 2. プリセット移動 (Ctrl + Tab または Alt + 右/左矢印)
    const isCtrlTab = e.ctrlKey && e.key === 'Tab';
    const isAltArrow = e.altKey && (e.key === 'ArrowRight' || e.key === 'ArrowLeft');
    
    if (isCtrlTab || isAltArrow) {
        e.preventDefault();
        const tabs = Array.from(doc.querySelectorAll('button[data-baseweb="tab"]'));
        let activeIdx = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
        if (activeIdx > -1) {
            let nextIdx;
            if (e.shiftKey || e.key === 'ArrowLeft') {
                nextIdx = (activeIdx - 1 + tabs.length) % tabs.length;
            } else {
                nextIdx = (activeIdx + 1) % tabs.length;
            }
            tabs[nextIdx].click();
        }
    }
    
    // 3. プリセット初期化 (Ctrl + Shift + R)
    if (e.ctrlKey && e.shiftKey && (e.key === 'r' || e.key === 'R')) {
        e.preventDefault();
        const activeTabPanel = doc.querySelector('div[data-baseweb="tab-panel"][aria-hidden="false"]');
        if (activeTabPanel) {
            const resetBtn = Array.from(activeTabPanel.querySelectorAll('button')).find(b => b.innerText.includes('🔄 プリセット'));
            if (resetBtn) resetBtn.click();
        }
    }
});

// オートコンプリートの無効化
setInterval(() => {
    doc.querySelectorAll('input').forEach(el => {
        el.setAttribute('autocomplete', 'new-password');
        if(!el.hasAttribute('data-randomized')) {
            el.setAttribute('name', Math.random().toString(36).substring(7));
            el.setAttribute('data-randomized', 'true');
        }
    });
}, 1000);
</script>
"""
components.html(js_code, height=0, width=0)

# ==========================================
# 2. 初期設定とセッション管理
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花, GUMI, 音街ウナ"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"

def get_default_preset():
    return {
        "mode": "⚡ 高速モード (yt-dlp使用 / API不要)", "title_mode": "✨ スッキリ出力",
        "yt_key": "", "gemini_key": "", "url": "", "exclude_words": "", "target_vocal": "", "multi_only": False,
        "min_v": 0, "max_v": 0, "min_c": 0, "max_c": 0,
        "add_lyrics": True, "add_analysis": False, "add_bpm": False
    }

if "logged_in_user" not in st.session_state:
    st.session_state.logged_in_user = None

if "hide_warning_forever" not in st.session_state:
    st.session_state.hide_warning_forever = False

if "presets" not in st.session_state:
    st.session_state.presets = {i: get_default_preset() for i in range(1, 11)}

if "results" not in st.session_state:
    st.session_state.results = {i: None for i in range(1, 11)}

# ==========================================
# 3. 上部ヘッダー（レスポンシブ対応＆お問い合わせ移動）
# ==========================================
col_title, col_link, col_contact, col_auth = st.columns([5, 1.5, 1.5, 2.5])

with col_title:
    st.title("🎶 楽曲抽出システム Ultimate")

with col_link:
    st.markdown("<div style='margin-top: 15px;'><a href='https://lit.link/_mitsu_3_' target='_blank'>👤 制作者のlit.link</a></div>", unsafe_allow_html=True)

with col_contact:
    st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
    with st.popover("✉️ お問い合わせ"):
        st.markdown("**バグ報告 / ご要望**")
        with st.form("contact_form"):
            subject_input = st.text_input("件名", placeholder="例：APIエラーについて")
            body_input = st.text_area("内容", placeholder="発生した問題やご要望をご記入ください。", height=100)
            submitted = st.form_submit_button("管理者に送信")
            if submitted:
                if subject_input and body_input:
                    try:
                        res = requests.post("https://formsubmit.co/ajax/yukimitsuyamamura0315@gmail.com", data={"件名": subject_input, "メッセージ": body_input, "_subject": f"【楽曲抽出】{subject_input}"})
                        if res.status_code == 200:
                            st.success("✅ 送信完了！")
                        else:
                            st.error("送信失敗。")
                    except:
                        st.error("通信エラー。")
                else:
                    st.warning("両方入力してください。")

with col_auth:
    st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
    with st.popover("⚙️ アカウント設定"):
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
                for pid, p_data in st.session_state.presets.items():
                    save_preset_to_db(st.session_state.logged_in_user, pid, p_data)
                st.success("保存完了！")
        else:
            log_mode = st.radio("メニュー", ["ログイン", "新規登録"], horizontal=True)
            u_name = st.text_input("ユーザー名")
            u_pass = st.text_input("パスワード", type="password")
            if log_mode == "新規登録":
                if st.button("登録", use_container_width=True):
                    if register_user(u_name, u_pass): st.success("登録完了！")
                    else: st.error("既に使用されています。")
            else:
                if st.button("ログイン", use_container_width=True):
                    if login_user(u_name, u_pass):
                        st.session_state.logged_in_user = u_name
                        loaded = load_presets_from_db(u_name)
                        for pid, p_data in loaded.items():
                            st.session_state.presets[pid].update(p_data)
                        st.session_state.hide_warning_forever = load_setting_from_db(u_name)
                        st.rerun()
                    else:
                        st.error("情報が違います。")

# ==========================================
# 4. ガイドとショートカット説明
# ==========================================
with st.expander("📖 詳しい使い方とショートカットキー"):
    st.markdown("""
    **【ショートカットキー (PC向け)】**
    *   **`Ctrl` + `Tab`** : 右のプリセットへ移動
    *   **`Ctrl` + `Shift` + `Tab`** : 左のプリセットへ移動
        *(※ブラウザの設定で効かない場合は、**`Alt` + `▶(右矢印)`** / **`Alt` + `◀(左矢印)`** をご使用ください)*
    *   **`Ctrl` + `Shift` + `R`** : 現在のプリセット設定を初期化する
    *   **`Enter`** : 次の入力項目へ移動
    """)

# ==========================================
# 5. データ処理・解析関数
# ==========================================
def parse_flexible_input(text):
    if not text: return []
    return [w.strip() for w in re.split(r'[,\n\s、]+', text) if w.strip()]

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

def clean_title(raw_title):
    title = str(raw_title)
    title = re.split(r'\s*[/／]\s*', title)[0]
    title = re.sub(r'\s+[^\s]*P\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r"(?i)[\(（\[【].*?(remix|bootleg|edit|mashup|flip|vip|cover|feat\.|long ver|short ver|MV|PV).*?[\)）\]】]", "", title)
    title = re.sub(r"【.*?】|\[.*?\]", "", title)
    title = re.split(r"(?i)\s+feat\.\s+|\s+ft\.\s+", title)[0]
    return title.strip()

def extract_vocals_ai(api_key, text_data):
    if not api_key: return "", ""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    以下の動画タイトルと概要欄から、純粋な「楽曲の題名」と「歌唱している合成音声名」を抽出。
    余計な文字は排除。ボーカル複数は「/」区切り。JSONのみ。
    {{"title": "純粋な曲名", "vocals": "合成音声名"}}
    【データ】
    {text_data}
    """
    try:
        response = model.generate_content(prompt)
        res_text = re.sub(r'`{3}(json)?', '', response.text, flags=re.IGNORECASE).strip()
        start = res_text.find('{')
        end = res_text.rfind('}')
        if start != -1 and end != -1:
            res_text = res_text[start:end+1]
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
        stats_res = stats_req.execute()
        stats_dict = {i["id"]: i["statistics"] for i in stats_res.get("items", [])}
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
            entries = info.get('entries', [info])
            for entry in entries:
                if entry and (entry.get('url') or entry.get('id')):
                    vid_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    videos.append({"曲名": entry.get('title', 'Unknown'), "概要欄データ": entry.get('description', ''), "URL": vid_url})
            return videos
    except Exception as e:
        raise ValueError(f"解析失敗: {e}")

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
                if not sub_df.empty and safe_vocal:
                    sub_df.to_excel(writer, sheet_name=safe_vocal, index=False)
            multi_df = df[df['合成音声'].astype(str).str.contains('/', na=False)]
            if not multi_df.empty:
                multi_df.to_excel(writer, sheet_name='複数人歌唱', index=False)
            vocs_order = ["初音ミク", "鏡音リン", "鏡音レン", "MEIKO", "KAITO", "GUMI", "音街ウナ", "可不", "星界", "重音テト"]
            vocs_order += [v for v in unique_vocals if v not in vocs_order]
            wb = writer.book
            ws = wb.add_worksheet('ボーカル横並び配置')
            header_fmt = wb.add_format({'bold': True, 'bg_color': '#D3D3D3'})
            col_offset = 0
            for vocal in vocs_order:
                sub_df = df[df['合成音声'].astype(str).str.contains(vocal, na=False, regex=False)]
                if sub_df.empty: continue
                ws.write(0, col_offset, f"【{vocal}】", header_fmt)
                for c_idx, col_name in enumerate(sub_df.columns):
                    ws.write(1, col_offset + c_idx, col_name, header_fmt)
                for r_idx, row in enumerate(sub_df.values):
                    for c_idx, val in enumerate(row):
                        ws.write(r_idx + 2, col_offset + c_idx, str(val))
                col_offset += len(sub_df.columns) + 5
    return output.getvalue()

# ==========================================
# 6. プリセットタブの描画ループ
# ==========================================
preset_tabs = st.tabs([f"プリセット {i}" for i in range(1, 11)])

def trigger_reset_preset(pid):
    st.session_state.presets[pid] = get_default_preset()
    st.session_state.results[pid] = None
    if st.session_state.logged_in_user:
        save_preset_to_db(st.session_state.logged_in_user, pid, st.session_state.presets[pid])

for i, tab in enumerate(preset_tabs):
    pid = i + 1
    with tab:
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
            st.warning("⚠️ 本当にこのプリセットの設定を初期化しますか？")
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
                    if st.session_state.logged_in_user:
                        save_setting_to_db(st.session_state.logged_in_user, True)

        p["mode"] = st.radio("抽出・解析モード", ["⚡ 高速モード (yt-dlp使用 / API不要)", "📊 統計フィルターモード (YouTube API使用)", "✨ AI完璧抽出モード (Gemini API使用 / 精度100%)"], index=["⚡ 高速モード (yt-dlp使用 / API不要)", "📊 統計フィルターモード (YouTube API使用)", "✨ AI完璧抽出モード (Gemini API使用 / 精度100%)"].index(p["mode"]), horizontal=True, key=f"mode_{pid}")
        p["title_mode"] = st.radio("曲名の出力モード", ["🔹 そのまま出力", "✨ スッキリ出力"], index=0 if p["title_mode"] == "🔹 そのまま出力" else 1, horizontal=True, key=f"tmode_{pid}")
        
        c1, c2 = st.columns(2)
        with c1: p["yt_key"] = st.text_input("YouTube API Key (統計モード用)", value=p["yt_key"], type="password", key=f"yk_{pid}")
        with c2: p["gemini_key"] = st.text_input("Gemini API Key (AIモード用)", value=p["gemini_key"], type="password", key=f"gk_{pid}")

        st.markdown("---")
        with st.expander("🔍 抽出条件・フィルター設定", expanded=True):
            st.markdown("**【数値フィルター】** ※枠内をクリックして直接数字を入力できます。")
            cv1, cv2 = st.columns(2)
            with cv1:
                p["min_v"] = st.number_input("最小再生数", value=p["min_v"], step=10000, key=f"minv_{pid}")
                p["max_v"] = st.number_input("最大再生数 (0で無制限)", value=p["max_v"], step=10000, key=f"maxv_{pid}")
            with cv2:
                p["min_c"] = st.number_input("最小コメント数", value=p["min_c"], step=100, key=f"minc_{pid}")
                p["max_c"] = st.number_input("最大コメント数 (0で無制限)", value=p["max_c"], step=100, key=f"maxc_{pid}")
            
            st.markdown("**【指定・除外設定】**(改行、カンマ、スペース区切り対応)")
            p["exclude_words"] = st.text_area("❌ この曲・ワードを除外して抽出", value=p["exclude_words"], placeholder="例: 初音ミクの消失, 踊ってみた\n歌ってみた", key=f"ex_{pid}")
            p["target_vocal"] = st.text_input("🎯 この合成音声の曲だけ抽出", value=p["target_vocal"], placeholder="例: 初音ミク, 鏡音リン", key=f"tv_{pid}")
            p["multi_only"] = st.checkbox("👥 複数人が歌唱している曲のみ抽出する", value=p["multi_only"], key=f"mo_{pid}")
            
            st.markdown("**【追加リンク】**")
            cl1, cl2, cl3 = st.columns(3)
            with cl1: p["add_lyrics"] = st.checkbox("📝 歌詞サイトリンク (Uta-Net優先)", value=p["add_lyrics"], key=f"al_{pid}")
            with cl2: p["add_analysis"] = st.checkbox("🤔 考察/Wikiリンク (初音ミクwiki優先)", value=p["add_analysis"], key=f"aa_{pid}")
            with cl3: p["add_bpm"] = st.checkbox("🎛️ BPM・Keyリンク", value=p["add_bpm"], key=f"ab_{pid}")

        p["url"] = st.text_area("🔗 プレイリストURLを入力 ※履歴を残しません", value=p["url"], height=68, key=f"url_{pid}")

        if st.button("🚀 抽出開始", type="primary", key=f"btn_{pid}"):
            if not p["url"].strip():
                st.warning("URLを入力してください。")
            else:
                with st.spinner(f"プリセット {pid} で解析を実行中..."):
                    try:
                        ex_list = parse_flexible_input(p["exclude_words"])
                        tv_list = parse_flexible_input(p["target_vocal"])
                        kw_list = [k.strip() for k in DEFAULT_KEYWORDS.split(',')]
                        ng_list = [n.strip() for n in DEFAULT_NG_WORDS.split(',')]
                        
                        raw_data = []
                        if "統計" in p["mode"]:
                            raw_data = get_youtube_playlist_api(p["yt_key"], p["url"].strip(), p["min_v"], p["max_v"], p["min_c"], p["max_c"])
                        else:
                            raw_data = get_playlist_ytdlp(p["url"].strip())

                        results = []
                        for item in raw_data:
                            raw_t = item["曲名"]
                            desc = item["概要欄データ"]
                            url = item["URL"]
                            
                            if any(ex in raw_t for ex in ex_list): continue
                                
                            if "AI" in p["mode"] and p["gemini_key"]:
                                clean_t, vocals = extract_vocals_ai(p["gemini_key"], f"{raw_t}\n{desc}")
                                if not clean_t: clean_t = clean_title(raw_t) if "スッキリ" in p["title_mode"] else raw_t
                            else:
                                clean_t = clean_title(raw_t) if "スッキリ" in p["title_mode"] else raw_t
                                vocals = extract_vocals_manual(raw_t, desc, kw_list, ng_list)
                            
                            if tv_list and not any(tv in vocals for tv in tv_list): continue
                            if p["multi_only"] and "/" not in vocals: continue
                            
                            safe_t = str(clean_t) if clean_t else "Unknown"
                            encoded = urllib.parse.quote(safe_t)
                            
                            row = {"曲名": clean_t, "合成音声": vocals, "URL": f'=HYPERLINK("{url}", "{url}")'}
                            
                            if p["add_lyrics"]: row["歌詞検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded}+歌詞+(site:uta-net.com+OR+site:w.atwiki.jp/hmiku)", "歌詞サイトを検索")'
                            if p["add_analysis"]: row["初音ミクwiki検索"] = f'=HYPERLINK("https://w.atwiki.jp/hmiku/search?andor=and&keyword={encoded}", "初音ミクwikiで見る")'
                            if p["add_bpm"]: row["BPM・キー検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded}+BPM+Key", "BPM/Keyを検索")'
                                
                            results.append(row)

                        df = pd.DataFrame(results)
                        if df.empty:
                            st.warning("条件に一致する楽曲がありませんでした。")
                            st.session_state.results[pid] = None
                        else:
                            st.session_state.results[pid] = df
                            if st.session_state.logged_in_user:
                                save_preset_to_db(st.session_state.logged_in_user, pid, p)
                    except Exception as e:
                        st.error(f"エラーが発生しました: {e}")
                        st.session_state.results[pid] = None

        saved_df = st.session_state.results[pid]
        if saved_df is not None and not saved_df.empty:
            st.success(f"✅ {len(saved_df)}曲の抽出結果 (プリセット {pid})")
            
            c_dl1, c_dl2 = st.columns(2)
            with c_dl1:
                excel_data = create_advanced_excel(saved_df)
                st.download_button("📥 XLSXダウンロード", excel_data, f"playlist_p{pid}.xlsx")
            with c_dl2:
                csv_df = saved_df.copy()
                for col in csv_df.columns:
                    if csv_df[col].dtype == object:
                        csv_df[col] = csv_df[col].apply(lambda x: re.search(r'"(https?://.*?)"', str(x)).group(1) if '=HYPERLINK' in str(x) else x)
                csv = csv_df.to_csv(index=False).encode('utf-8-sig')
                st.download_button("📥 CSVダウンロード", csv, f"playlist_p{pid}.csv", "text/csv")
            
            # JavaScriptによる2秒タイマー付きのスマートコピーボタン
            b64_csv = base64.b64encode(csv_df.to_csv(index=False, sep='\t').encode('utf-8')).decode('utf-8')
            copy_html = f"""
            <button id="copyBtn{pid}" onclick="copyData{pid}()" style="padding: 10px 20px; background-color: #2e7d32; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; font-size: 14px; margin-bottom: 5px;">
                📋 表データをクリップボードにコピー
            </button>
            <script>
            function copyData{pid}() {{
                const btn = document.getElementById("copyBtn{pid}");
                const str = decodeURIComponent(escape(window.atob('{b64_csv}')));
                navigator.clipboard.writeText(str).then(function() {{
                    btn.innerHTML = "✅ コピーしました！";
                    btn.style.backgroundColor = "#1b5e20";
                    // 正確に2000ミリ秒(2秒)後に元の状態に戻す
                    setTimeout(function() {{
                        btn.innerHTML = "📋 表データをクリップボードにコピー";
                        btn.style.backgroundColor = "#2e7d32";
                    }}, 2000);
                }});
            }}
            </script>
            """
            components.html(copy_html, height=50)
            
            # 高さをデータ行数から自動計算して全件表示（スクロールバーを消す）
            # 1行あたり約35px + ヘッダー分で計算
            total_height = (len(saved_df) * 35) + 20
            st.dataframe(saved_df, height=total_height, use_container_width=True)
