import os
# --- エラー回避: Google APIのGCEメタデータ取得タイムアウトを無効化 ---
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = ""
os.environ["NO_GCE_CHECK"] = "true"
os.environ["GOOGLE_API_USE_MTLS_ENDPOINT"] = "never"

import streamlit as st
import pandas as pd
import re
import requests
import json
import urllib.parse
import urllib.request
import sqlite3
import hashlib
from googleapiclient.discovery import build
import google.generativeai as genai
from io import BytesIO
import yt_dlp
import streamlit.components.v1 as components

# ==========================================
# 0. データベース初期化＆ユーザー認証関数
# ==========================================
DB_FILE = "extractor_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # ユーザーテーブル
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY, password TEXT, show_warning BOOLEAN)''')
    # プリセットテーブル
    c.execute('''CREATE TABLE IF NOT EXISTS presets
                 (username TEXT, preset_id INTEGER, data TEXT,
                  PRIMARY KEY(username, preset_id))''')
    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username, password):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password, show_warning) VALUES (?, ?, ?)", 
                  (username, hash_password(password), True))
        # 新規登録時に10個の空プリセットを作成
        default_preset = json.dumps(get_default_preset())
        for i in range(1, 11):
            c.execute("INSERT INTO presets (username, preset_id, data) VALUES (?, ?, ?)", 
                      (username, i, default_preset))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def login_user(username, password):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT show_warning FROM users WHERE username=? AND password=?", 
              (username, hash_password(password)))
    user = c.fetchone()
    conn.close()
    if user:
        return {"username": username, "show_warning": bool(user[0])}
    return None

def update_warning_setting(username, show_warning):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET show_warning=? WHERE username=?", (show_warning, username))
    conn.commit()
    conn.close()
    st.session_state.user['show_warning'] = show_warning

def save_preset_to_db(username, preset_id, data_dict):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE presets SET data=? WHERE username=? AND preset_id=?", 
              (json.dumps(data_dict), username, preset_id))
    conn.commit()
    conn.close()

def load_presets_from_db(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT preset_id, data FROM presets WHERE username=?", (username,))
    rows = c.fetchall()
    conn.close()
    presets = {}
    for r in rows:
        presets[r[0]] = json.loads(r[1])
    return presets

def get_default_preset():
    return {
        "mode": "⚡ 高速モード (yt-dlp使用 / API不要)", "title_mode": "✨ スッキリ出力",
        "yt_key": "", "gemini_key": "", "url": "", "exclude_words": "", "target_vocal": "", "multi_only": False,
        "min_v": 0, "max_v": 0, "min_c": 0, "max_c": 0,
        "add_lyrics": True, "add_analysis": False, "add_bpm": False, "add_rights": False
    }

init_db()

# ==========================================
# 1. UIカスタマイズ＆JavaScript
# ==========================================
st.set_page_config(page_title="楽曲抽出＆特定システム Pro", layout="wide")
hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.st-emotion-cache-1wbqy5l {display: none;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

js_code = """
<script>
const doc = window.parent.document;
doc.addEventListener('keydown', function(e) {
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
});
setInterval(() => {
    doc.querySelectorAll('input').forEach(el => {
        el.setAttribute('autocomplete', 'new-password');
        el.setAttribute('name', Math.random().toString(36).substring(7));
    });
}, 1000);
</script>
"""
components.html(js_code, height=0, width=0)

# ==========================================
# 2. セッション管理
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花, GUMI, 音街ウナ"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"

if 'user' not in st.session_state:
    st.session_state.user = None
if 'presets' not in st.session_state:
    st.session_state.presets = {i: get_default_preset() for i in range(1, 11)}
if 'results' not in st.session_state:
    st.session_state.results = {i: None for i in range(1, 11)}

# ==========================================
# 3. サイドバー (ログイン・設定)
# ==========================================
with st.sidebar:
    st.header("👤 ユーザーアカウント")
    if st.session_state.user is None:
        login_tab, reg_tab = st.tabs(["ログイン", "新規登録"])
        with login_tab:
            l_user = st.text_input("ユーザー名", key="l_user")
            l_pass = st.text_input("パスワード", type="password", key="l_pass")
            if st.button("ログイン"):
                user_data = login_user(l_user, l_pass)
                if user_data:
                    st.session_state.user = user_data
                    st.session_state.presets = load_presets_from_db(l_user)
                    st.success("ログインしました！")
                    st.rerun()
                else:
                    st.error("ユーザー名かパスワードが違います。")
        with reg_tab:
            r_user = st.text_input("新規ユーザー名", key="r_user")
            r_pass = st.text_input("新規パスワード", type="password", key="r_pass")
            if st.button("登録してログイン"):
                if create_user(r_user, r_pass):
                    st.session_state.user = login_user(r_user, r_pass)
                    st.session_state.presets = load_presets_from_db(r_user)
                    st.success("登録完了しました！")
                    st.rerun()
                else:
                    st.error("そのユーザー名は既に使用されています。")
    else:
        st.write(f"**{st.session_state.user['username']}** さん、こんにちは！")
        
        # 警告表示の設定トグル
        current_warning_pref = st.session_state.user['show_warning']
        new_warning_pref = st.checkbox("初期化時の警告を表示する", value=current_warning_pref)
        if new_warning_pref != current_warning_pref:
            update_warning_setting(st.session_state.user['username'], new_warning_pref)
            
        if st.button("ログアウト"):
            st.session_state.user = None
            st.session_state.presets = {i: get_default_preset() for i in range(1, 11)}
            st.session_state.results = {i: None for i in range(1, 11)}
            st.rerun()

col_title, col_link = st.columns([4, 1])
with col_title:
    st.title("🎶 楽曲抽出システム Pro")
with col_link:
    st.write("\n")
    st.markdown("[👤 制作者 (Mitsu) の lit.link](https://lit.link/_mitsu_3_)")

# ==========================================
# 4. データ処理関数
# ==========================================
def parse_flexible_input(text):
    """改行、カンマ、空白のどれでもリスト化する"""
    if not text: return []
    words = re.split(r'[\n,、\s]+', text)
    return [w.strip() for w in words if w.strip()]

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
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    以下の動画タイトルと概要欄から、純粋な「楽曲の題名」と「歌唱している合成音声名(ボーカロイド等)」を抽出してください。
    - オリジナルPV、MV、〇〇P、long ver、各種記号、feat. などの余計な文字列は完全に排除。
    - ボーカルが複数の場合は「/」で区切る。
    - 結果は以下のJSONフォーマットのみで出力。Markdown不要。
    {{"title": "純粋な曲名", "vocals": "合成音声名"}}
    
    【データ】
    {text_data}
    """
    try:
        response = model.generate_content(prompt)
        res_text = re.sub(r'^```json\s*', '', response.text, flags=re.M)
        res_text = re.sub(r'^```\s*', '', res_text, flags=re.M)
        start, end = res_text.find('{'), res_text.rfind('}')
        if start != -1 and end != -1: res_text = res_text[start:end+1]
        result = json.loads(res_text)
        return result.get("title", ""), result.get("vocals", "")
    except: return "", ""

def judge_rights_ai(api_key, title, vocals, desc):
    """AIによる簡易権利判定"""
    if not api_key: return "判定不能（Gemini APIキー未設定）"
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    以下の楽曲情報から、この曲をDJイベントや大学祭で使用する際の著作権の傾向を推測し、50文字以内で簡潔に回答してください。
    (例: 「ボカロPの自主管理楽曲の可能性が高いため、本人の規約確認を推奨します。」「JASRAC信託の商業楽曲の可能性が高いです。」等)
    曲名: {title} / ボーカル: {vocals}
    概要欄抜粋: {desc[:200]}
    """
    try:
        return model.generate_content(prompt).text.strip()
    except: return "判定エラー"

def get_youtube_playlist_api(api_key, url, min_v, max_v, min_c, max_c):
    match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
    if not match: raise ValueError("有効なプレイリストIDが見つかりません。")
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
    with pd.ExcelWriter(output, engine='xlsxwriter', options={'strings_to_urls': False}) as writer:
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
# 5. UI: 初期化ダイアログ (Streamlit 1.34+)
# ==========================================
@st.experimental_dialog("⚠️ 設定の初期化")
def confirm_reset_dialog(pid):
    st.write(f"プリセット {pid} の設定を初期化しますか？")
    disable_warning = st.checkbox("次回からこの警告を表示しない")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("はい、初期化します", type="primary", use_container_width=True):
            # 警告設定の更新処理
            if disable_warning and st.session_state.user:
                update_warning_setting(st.session_state.user['username'], False)
            elif disable_warning and not st.session_state.user:
                st.session_state.user = {'show_warning': False} # ゲスト用モック
            
            # 初期化処理
            st.session_state.presets[pid] = get_default_preset()
            st.session_state.results[pid] = None
            if st.session_state.user and 'username' in st.session_state.user:
                save_preset_to_db(st.session_state.user['username'], pid, st.session_state.presets[pid])
            st.rerun()
    with col2:
        if st.button("キャンセル", use_container_width=True):
            st.rerun()

# ==========================================
# 6. プリセットタブの描画ループ
# ==========================================
preset_tabs = st.tabs([f"プリセット {i}" for i in range(1, 11)])

for i, tab in enumerate(preset_tabs):
    pid = i + 1
    with tab:
        p = st.session_state.presets[pid]
        
        col_btn1, col_btn2 = st.columns([2, 8])
        with col_btn1:
            if st.button("🔄 設定を初期化", key=f"reset_{pid}"):
                show_warn = True
                if st.session_state.user and 'show_warning' in st.session_state.user:
                    show_warn = st.session_state.user['show_warning']
                
                if show_warn:
                    confirm_reset_dialog(pid)
                else:
                    st.session_state.presets[pid] = get_default_preset()
                    st.session_state.results[pid] = None
                    if st.session_state.user and 'username' in st.session_state.user:
                        save_preset_to_db(st.session_state.user['username'], pid, st.session_state.presets[pid])
                    st.rerun()

        # UI構築
        p["mode"] = st.radio("抽出・解析モード", ["⚡ 高速モード (yt-dlp使用 / API不要)", "📊 統計フィルターモード (YouTube API使用)", "✨ AI完璧抽出モード (Gemini API使用 / 精度100%)"], index=["⚡ 高速モード (yt-dlp使用 / API不要)", "📊 統計フィルターモード (YouTube API使用)", "✨ AI完璧抽出モード (Gemini API使用 / 精度100%)"].index(p["mode"]), horizontal=True, key=f"mode_{pid}")
        p["title_mode"] = st.radio("曲名の出力モード", ["🔹 そのまま出力", "✨ スッキリ出力"], index=0 if p["title_mode"] == "🔹 そのまま出力" else 1, horizontal=True, key=f"tmode_{pid}")
        
        c1, c2 = st.columns(2)
        with c1: p["yt_key"] = st.text_input("YouTube API Key (統計モード用)", value=p["yt_key"], key=f"yk_{pid}")
        with c2: p["gemini_key"] = st.text_input("Gemini API Key (AI権利判定・AI抽出用)", value=p["gemini_key"], key=f"gk_{pid}")

        st.markdown("---")
        with st.expander("🔍 抽出条件・フィルター設定", expanded=True):
            cv1, cv2 = st.columns(2)
            with cv1:
                p["min_v"] = st.number_input("最小再生数", value=p["min_v"], step=10000, key=f"minv_{pid}")
                p["max_v"] = st.number_input("最大再生数 (0で無制限)", value=p["max_v"], step=10000, key=f"maxv_{pid}")
            with cv2:
                p["min_c"] = st.number_input("最小コメント数", value=p["min_c"], step=100, key=f"minc_{pid}")
                p["max_c"] = st.number_input("最大コメント数 (0で無制限)", value=p["max_c"], step=100, key=f"maxc_{pid}")
            
            p["exclude_words"] = st.text_area("❌ 除外ワード (改行・カンマ・スペース区切り)", value=p["exclude_words"], placeholder="例: 初音ミクの消失 踊ってみた", key=f"ex_{pid}")
            p["target_vocal"] = st.text_input("🎯 指定合成音声 (改行・カンマ・スペース区切り)", value=p["target_vocal"], placeholder="例: 初音ミク 鏡音リン", key=f"tv_{pid}")
            p["multi_only"] = st.checkbox("👥 複数人歌唱のみ抽出", value=p["multi_only"], key=f"mo_{pid}")
            
            cl1, cl2, cl3, cl4 = st.columns(4)
            with cl1: p["add_lyrics"] = st.checkbox("📝 歌詞リンク(Wiki等)", value=p["add_lyrics"], key=f"al_{pid}")
            with cl2: p["add_analysis"] = st.checkbox("🤔 考察リンク", value=p["add_analysis"], key=f"aa_{pid}")
            with cl3: p["add_bpm"] = st.checkbox("🎛️ BPM・Keyリンク", value=p["add_bpm"], key=f"ab_{pid}")
            with cl4: p["add_rights"] = st.checkbox("⚖️ JASRAC等 権利確認リンク & AI判定", value=p.get("add_rights", False), key=f"ar_{pid}")

        p["url"] = st.text_area("🔗 プレイリストURLを入力 (履歴非表示)", value=p["url"], height=68, key=f"url_{pid}")

        # 抽出ボタン (押下時にDB保存)
        if st.button("🚀 抽出開始", type="primary", key=f"btn_{pid}"):
            if st.session_state.user and 'username' in st.session_state.user:
                save_preset_to_db(st.session_state.user['username'], pid, p)
                
            if not p["url"].strip():
                st.warning("URLを入力してください。")
            else:
                with st.spinner(f"プリセット {pid} で解析を実行中..."):
                    try:
                        ex_list = parse_flexible_input(p["exclude_words"])
                        tv_list = parse_flexible_input(p["target_vocal"])
                        kw_list = parse_flexible_input(DEFAULT_KEYWORDS)
                        ng_list = parse_flexible_input(DEFAULT_NG_WORDS)
                        
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
                            
                            if p["add_lyrics"]: row["歌詞検索(Wiki等)"] = f'=HYPERLINK("https://w.atwiki.jp/hmiku/search?keyword={encoded}", "初音ミクWikiで検索")'
                            if p["add_analysis"]: row["考察検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded}+考察", "考察を検索")'
                            if p["add_bpm"]: row["BPM・キー検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded}+BPM+Key", "BPM/Keyを検索")'
                            
                            if p["add_rights"]:
                                row["JASRAC検索(J-WID)"] = f'=HYPERLINK("https://www2.jasrac.or.jp/eJwid/", "J-WIDを開く")'
                                row["NexTone検索"] = f'=HYPERLINK("https://search.nex-tone.co.jp/terms", "NexToneを開く")'
                                row["権利AI判定"] = judge_rights_ai(p["gemini_key"], safe_t, vocals, desc) if p["gemini_key"] else "APIキー未設定"
                                
                            results.append(row)

                        df = pd.DataFrame(results)
                        if df.empty:
                            st.warning("条件に一致する楽曲がありませんでした。")
                            st.session_state.results[pid] = None
                        else:
                            st.session_state.results[pid] = df
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
            
            with st.expander("📋 ワンクリックで表データをコピーする"):
                st.code(csv_df.to_csv(index=False, sep='\t'), language='csv')
            
            st.dataframe(saved_df)
