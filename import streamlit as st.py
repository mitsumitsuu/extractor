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
# 0. メール送信設定 (ご自身の情報に変更してください)
# ==========================================
SENDER_EMAIL = "your_email@gmail.com" # 送信元のGmailアドレス
SENDER_PASSWORD = "your_app_password" # Gmailのアプリパスワード（16桁）

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

# パスワードリセット関連
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
    
    reset_link = f"https://your-app-url.com/?token={token}" # 実際のアプリURLに変更してください
    base_text = f"パスワードの再設定リクエストを受け付けました。以下のリンクをクリックして新しいパスワードを設定してください。\n\nユーザー名: {username}\nリセットリンク: {reset_link}\n\n※このリンクは1時間有効です。"
    
    # Geminiを使ってユーザーの言語に翻訳
    if gemini_key and language != "日本語":
        try:
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            translated = model.generate_content(f"Translate the following email into {language}. Keep the links intact:\n{base_text}").text
            body = translated
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
    except Exception as e:
        return False

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
</script>
<script type="text/javascript" src="//translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
"""
components.html(js_code, height=0, width=0)

# ==========================================
# 3. 初期設定とセッション管理
# ==========================================
if "logged_in_user" not in st.session_state: st.session_state.logged_in_user = None
if "sys_language" not in st.session_state: st.session_state.sys_language = "日本語"

def get_default_preset():
    return {
        "mode": "⚡ 高速モード (yt-dlp使用 / API不要)", "title_mode": "✨ スッキリ出力",
        "yt_key": "", "gemini_key": "", "url": "", "exclude_words": "", "target_vocal": "", "target_producer": "",
        "multi_only": False, "min_v": 0, "max_v": 0, "min_c": 0, "max_c": 0,
        "add_lyrics": True, "add_analysis": False, "add_bpm": True, "filename": "playlist"
    }

if "presets" not in st.session_state: st.session_state.presets = {i: get_default_preset() for i in range(1, 11)}
if "results" not in st.session_state: st.session_state.results = {i: None for i in range(1, 11)}

# パスワードリセットトークン処理
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
# 4. ヘッダー（翻訳・寄付・アカウント）
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
# 5. データ処理関数 (AI検索機能追加)
# ==========================================
def parse_flexible_input(text):
    return [w.strip() for w in re.split(r'[,\n\s、]+', text) if w.strip()]

def search_youtube_by_title(title):
    """yt-dlpのytsearchを用いて曲名から最も関連性の高い動画URLを取得"""
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
    """ペーストされたランキング等から曲名リストをAIで抽出"""
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

# その他既存関数（clean_title等）の省略（前バージョンと同じロジックを継承）
def clean_title(raw_title):
    title = str(raw_title)
    title = re.split(r'\s*[/／]\s*', title)[0]
    title = re.sub(r'\s+[^\s]*P\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r"(?i)[\(（\[【].*?(remix|bootleg|edit|mashup|flip|vip|cover|feat\.|long ver|short ver|MV|PV).*?[\)）\]】]", "", title)
    title = re.sub(r"【.*?】|\[.*?\]", "", title)
    title = re.split(r"(?i)\s+feat\.\s+|\s+ft\.\s+", title)[0]
    return title.strip()

def make_hyperlink(url, text):
    formula = f'=HYPERLINK("{url}", "{text}")'
    return formula if len(formula) <= 255 else url

def create_advanced_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter', engine_kwargs={'options': {'strings_to_urls': False}}) as writer:
        df.to_excel(writer, sheet_name='一括データ', index=False)
        for worksheet in writer.book.worksheets():
            try: worksheet.autofit()
            except: pass
    return output.getvalue()

# ==========================================
# 6. ガイド・お問い合わせ (最初から開く)
# ==========================================
with st.expander("📖 詳しい使い方とショートカットキー / ✉️ お問い合わせ", expanded=True):
    col_guide, col_contact = st.columns([1, 1])
    with col_guide:
        st.markdown("""
        **【ショートカットキー (PC)】**
        *   `Ctrl`+`Shift`+`▶` : 右のプリセットへ / `Ctrl`+`Shift`+`◀` : 左へ
        *   `Ctrl`+`Shift`+`R` : 現在のプリセット初期化
        *   `Enter` : 次の入力項目へ移動
        
        **【新機能: コピペ解析 (AI)】**
        BillboardやSNSのランキング文字をそのまま貼り付けて抽出開始すると、AIが曲名を認識し自動でYouTube動画を探し出してプレイリスト化します。
        """)
    with col_contact:
        with st.form("contact_form_top"):
            subject_input = st.text_input("件名", placeholder="バグ報告・要望")
            body_input = st.text_area("内容", height=100)
            if st.form_submit_button("管理者に送信"):
                try: requests.post("https://formsubmit.co/ajax/yukimitsuyamamura0315@gmail.com", data={"件名": subject_input, "メッセージ": body_input})
                except: pass

# ==========================================
# 7. プリセットタブ
# ==========================================
preset_tabs = st.tabs([f"プリセット {i}" for i in range(1, 11)])

for i in range(10):
    pid = i + 1
    with preset_tabs[i]:
        p = st.session_state.presets[pid]
        
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
        with st.expander("⚙️ 抽出条件・詳細フィルター"):
            p["mode"] = st.radio("抽出モード", ["⚡ 高速モード", "✨ AI完璧抽出モード (Gemini必須)"], horizontal=True, key=f"m_{pid}")
            c1, c2 = st.columns(2)
            with c1: p["yt_key"] = st.text_input("YouTube API Key", type="password", key=f"yk_{pid}")
            with c2: p["gemini_key"] = st.text_input("Gemini API Key", type="password", key=f"gk_{pid}")
            
            p["exclude_words"] = st.text_input("❌ 除外ワード", value=p.get("exclude_words", ""), key=f"ex_{pid}")
            p["filename"] = st.text_input("📄 出力ファイル名", value=p.get("filename", "playlist"), key=f"fn_{pid}")
            p["add_lyrics"] = st.checkbox("📝 歌詞リンク追加", value=p["add_lyrics"], key=f"al_{pid}")

        # 抽出＆プレイリスト生成
        col_exec, col_pl = st.columns([1, 1])
        with col_exec:
            if st.button("🚀 抽出開始", type="primary", use_container_width=True, key=f"btn_{pid}"):
                with st.spinner(f"プリセット {pid} 実行中..."):
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
                        raw_data = get_playlist_ytdlp(p["url"].strip())
                        for item in raw_data:
                            clean_t = clean_title(item["曲名"])
                            row = {"曲名": clean_t, "合成音声": "手動抽出", "URL": make_hyperlink(item["URL"], item["URL"])}
                            if p["add_lyrics"]: row["歌詞検索"] = make_hyperlink(f"https://www.uta-net.com/search/?keyword={urllib.parse.quote(clean_t)}", "Uta-Net")
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
                        except: st.error("ファイル読み込みエラー")

                    if results:
                        st.session_state.results[pid] = pd.DataFrame(results)
                    else: st.warning("データが見つかりませんでした。")

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

        # 結果表示エリア
        if saved_df is not None and not saved_df.empty:
            st.success(f"✅ {len(saved_df)}曲抽出")
            excel_data = create_advanced_excel(saved_df)
            st.download_button("📥 XLSXを保存", excel_data, f"{p.get('filename', 'playlist')}.xlsx", key=f"dl_{pid}")
            st.dataframe(saved_df, height=300, use_container_width=True)
