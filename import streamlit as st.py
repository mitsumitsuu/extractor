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

# ==========================================
# 1. UIカスタマイズ＆JavaScript強制注入
# ==========================================
st.set_page_config(page_title="楽曲抽出＆特定システム Pro", layout="wide")

# Streamlitの不要なヘッダー・フッター・GitHubアイコンを非表示
hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.st-emotion-cache-1wbqy5l {display: none;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# Enterキーでのフォーカス移動とオートコンプリート履歴の無効化をブラウザに強制するJS
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
// 履歴（予測変換）を完全に消去
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
# 2. 初期設定とセッション管理 (プリセット10個)
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花, GUMI, 音街ウナ"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"

# 初期プリセットデータの作成
if "presets" not in st.session_state:
    st.session_state.presets = {}
    for i in range(1, 11):
        st.session_state.presets[i] = {
            "mode": "⚡ 高速モード (yt-dlp使用 / API不要)",
            "title_mode": "✨ スッキリ出力",
            "yt_key": "", "gemini_key": "", "url": "",
            "exclude_words": "", "target_vocal": "", "multi_only": False,
            "min_v": 0, "max_v": 0, "min_c": 0, "max_c": 0,
            "add_lyrics": True, "add_analysis": False, "add_bpm": False
        }
# 各プリセットの抽出結果保存用
if "results" not in st.session_state:
    st.session_state.results = {i: None for i in range(1, 11)}

col_title, col_link = st.columns([4, 1])
with col_title:
    st.title("🎶 楽曲抽出システム Pro (プリセット対応版)")
with col_link:
    st.write("\n")
    st.markdown("[👤 制作者 (Mitsu) の lit.link](https://lit.link/_mitsu_3_)")

# ==========================================
# 3. データ処理・解析関数
# ==========================================
def extract_vocals_manual(title, description, keywords, ng_list):
    """手動モードでのボーカル抽出"""
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
    """曲名を綺麗に整える（不要な記号や〇〇Pを除去）"""
    title = str(raw_title)
    title = re.split(r'\s*[/／]\s*', title)[0]
    title = re.sub(r'\s+[^\s]*P\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r"(?i)[\(（\[【].*?(remix|bootleg|edit|mashup|flip|vip|cover|feat\.|long ver|short ver|MV|PV).*?[\)）\]】]", "", title)
    title = re.sub(r"【.*?】|\[.*?\]", "", title)
    title = re.split(r"(?i)\s+feat\.\s+|\s+ft\.\s+", title)[0]
    return title.strip()

def extract_vocals_ai(api_key, text_data):
    """Geminiによる完全精度抽出（エラー回避強化版）"""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    以下の動画タイトルと概要欄から、純粋な「楽曲の題名」と「歌唱している合成音声名(ボーカロイド等)」を抽出してください。
    - オリジナルPV、MV、〇〇P、long ver、各種記号、feat. などの余計な文字列は完全に排除すること。
    - ボーカルが複数の場合は「/」で区切ること。
    - 結果は以下のJSONフォーマットのみで出力すること。Markdown不要。
    {{"title": "純粋な曲名", "vocals": "合成音声名"}}
    
    【データ】
    {text_data}
    """
    try:
        response = model.generate_content(prompt)
        res_text = response.text
        # Markdownコードブロックを安全に消去
        res_text = re.sub(r'^```json\s*', '', res_text, flags=re.M)
        res_text = re.sub(r'^```\s*', '', res_text, flags=re.M)
        
        start = res_text.find('{')
        end = res_text.rfind('}')
        if start != -1 and end != -1:
            res_text = res_text[start:end+1]
            
        result = json.loads(res_text)
        return result.get("title", ""), result.get("vocals", "")
    except Exception:
        return "", ""

def get_youtube_playlist_api(api_key, url, min_v, max_v, min_c, max_c):
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
    """Excel専用の高度なフォーマット（リンク化・シート分割・間隔配置）を実行"""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter', options={'strings_to_urls': False}) as writer:
        
        # 1. 一括データ
        df.to_excel(writer, sheet_name='一括データ', index=False)
        
        if "合成音声" in df.columns:
            # 独自抽出したユニークボーカルリスト
            unique_vocals = set()
            for v in df['合成音声'].dropna():
                for part in str(v).split('/'):
                    if part.strip() and part.strip() != "手動抽出モード": unique_vocals.add(part.strip())
            
            # 2. 合成音声ごとのシート
            for vocal in unique_vocals:
                safe_vocal = re.sub(r'[\\/*?:\[\]]', '', vocal)[:31]
                sub_df = df[df['合成音声'].astype(str).str.contains(vocal, na=False, regex=False)]
                if not sub_df.empty and safe_vocal:
                    sub_df.to_excel(writer, sheet_name=safe_vocal, index=False)
                    
            # 3. 複数人歌唱シート
            multi_df = df[df['合成音声'].astype(str).str.contains('/', na=False)]
            if not multi_df.empty:
                multi_df.to_excel(writer, sheet_name='複数人歌唱', index=False)
                
            # 4. ボーカル別間隔配置シート (5列空けて横並び)
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
# 4. プリセットタブの描画ループ
# ==========================================
preset_tabs = st.tabs([f"プリセット {i}" for i in range(1, 11)])

def reset_preset(pid):
    st.session_state.presets[pid] = {
        "mode": "⚡ 高速モード (yt-dlp使用 / API不要)", "title_mode": "✨ スッキリ出力",
        "yt_key": "", "gemini_key": "", "url": "", "exclude_words": "", "target_vocal": "", "multi_only": False,
        "min_v": 0, "max_v": 0, "min_c": 0, "max_c": 0,
        "add_lyrics": True, "add_analysis": False, "add_bpm": False
    }
    st.session_state.results[pid] = None

for i, tab in enumerate(preset_tabs):
    pid = i + 1
    with tab:
        p = st.session_state.presets[pid]
        
        col_btn1, col_btn2 = st.columns([1, 8])
        with col_btn1:
            if st.button("🔄 設定を初期化", key=f"reset_{pid}"):
                reset_preset(pid)
                st.rerun()

        # UI構築
        p["mode"] = st.radio("抽出・解析モード", ["⚡ 高速モード (yt-dlp使用 / API不要)", "📊 統計フィルターモード (YouTube API使用)", "✨ AI完璧抽出モード (Gemini API使用 / 精度100%)"], index=["⚡ 高速モード (yt-dlp使用 / API不要)", "📊 統計フィルターモード (YouTube API使用)", "✨ AI完璧抽出モード (Gemini API使用 / 精度100%)"].index(p["mode"]), horizontal=True, key=f"mode_{pid}")
        p["title_mode"] = st.radio("曲名の出力モード", ["🔹 そのまま出力", "✨ スッキリ出力"], index=0 if p["title_mode"] == "🔹 そのまま出力" else 1, horizontal=True, key=f"tmode_{pid}")
        
        c1, c2 = st.columns(2)
        with c1: p["yt_key"] = st.text_input("YouTube API Key (統計モード用)", value=p["yt_key"], key=f"yk_{pid}")
        with c2: p["gemini_key"] = st.text_input("Gemini API Key (AIモード用)", value=p["gemini_key"], key=f"gk_{pid}")

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
            
            st.markdown("**【指定・除外設定】**")
            p["exclude_words"] = st.text_area("❌ この曲・ワードを除外して抽出 (改行区切り)", value=p["exclude_words"], placeholder="例:\n初音ミクの消失\n踊ってみた", key=f"ex_{pid}")
            p["target_vocal"] = st.text_input("🎯 この合成音声の曲だけ抽出 (カンマ区切り)", value=p["target_vocal"], placeholder="例: 初音ミク, 鏡音リン", key=f"tv_{pid}")
            p["multi_only"] = st.checkbox("👥 複数人が歌唱している曲のみ抽出する", value=p["multi_only"], key=f"mo_{pid}")
            
            st.markdown("**【追加リンク】**")
            cl1, cl2, cl3 = st.columns(3)
            with cl1: p["add_lyrics"] = st.checkbox("📝 歌詞リンク", value=p["add_lyrics"], key=f"al_{pid}")
            with cl2: p["add_analysis"] = st.checkbox("🤔 考察リンク", value=p["add_analysis"], key=f"aa_{pid}")
            with cl3: p["add_bpm"] = st.checkbox("🎛️ BPM・Keyリンク", value=p["add_bpm"], key=f"ab_{pid}")

        p["url"] = st.text_input("🔗 プレイリストURLを入力", value=p["url"], key=f"url_{pid}")

        # 抽出ボタン
        if st.button("🚀 抽出開始", type="primary", key=f"btn_{pid}"):
            if not p["url"].strip():
                st.warning("URLを入力してください。")
            else:
                with st.spinner(f"プリセット {pid} で解析を実行中..."):
                    try:
                        ex_list = [w.strip() for w in p["exclude_words"].split('\n') if w.strip()]
                        tv_list = [v.strip() for v in p["target_vocal"].split(',') if v.strip()]
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
                            
                            # 除外判定
                            if any(ex in raw_t for ex in ex_list): continue
                                
                            # AI or 手動抽出
                            if "AI" in p["mode"] and p["gemini_key"]:
                                clean_t, vocals = extract_vocals_ai(p["gemini_key"], f"{raw_t}\n{desc}")
                                if not clean_t: clean_t = clean_title(raw_t) if "スッキリ" in p["title_mode"] else raw_t
                            else:
                                clean_t = clean_title(raw_t) if "スッキリ" in p["title_mode"] else raw_t
                                vocals = extract_vocals_manual(raw_t, desc, kw_list, ng_list)
                            
                            # 指定ボーカル判定
                            if tv_list and not any(tv in vocals for tv in tv_list): continue
                            # 複数人判定
                            if p["multi_only"] and "/" not in vocals: continue
                            
                            safe_t = str(clean_t) if clean_t else "Unknown"
                            encoded = urllib.parse.quote(safe_t)
                            
                            # Excelで直接飛べるHYPERLINK関数を構築
                            row = {"曲名": clean_t, "合成音声": vocals, "URL": f'=HYPERLINK("{url}", "{url}")'}
                            if p["add_lyrics"]: row["歌詞検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded}+歌詞", "歌詞を検索")'
                            if p["add_analysis"]: row["考察検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded}+考察", "考察を検索")'
                            if p["add_bpm"]: row["BPM・キー検索"] = f'=HYPERLINK("https://www.google.com/search?q={encoded}+BPM+Key", "BPM/Keyを検索")'
                                
                            results.append(row)

                        df = pd.DataFrame(results)
                        if df.empty:
                            st.warning("条件に一致する楽曲がありませんでした。除外設定や数値を緩めてください。")
                            st.session_state.results[pid] = None
                        else:
                            st.session_state.results[pid] = df
                    except Exception as e:
                        st.error(f"エラーが発生しました: {e}")
                        st.session_state.results[pid] = None

        # セッションに結果が保存されている場合は常に表示（タブを移動しても消えない）
        saved_df = st.session_state.results[pid]
        if saved_df is not None and not saved_df.empty:
            st.success(f"✅ {len(saved_df)}曲の抽出結果 (プリセット {pid})")
            
            # XLSX / CSVの順でダウンロードボタン
            c_dl1, c_dl2 = st.columns(2)
            with c_dl1:
                excel_data = create_advanced_excel(saved_df)
                st.download_button("📥 XLSXダウンロード (シート分割・横並び自動生成)", excel_data, f"playlist_p{pid}.xlsx")
            with c_dl2:
                # CSV用にHYPERLINK関数を除去した綺麗なDFを作成
                csv_df = saved_df.copy()
                for col in csv_df.columns:
                    if csv_df[col].dtype == object:
                        csv_df[col] = csv_df[col].apply(lambda x: re.search(r'"(https?://.*?)"', str(x)).group(1) if '=HYPERLINK' in str(x) else x)
                csv = csv_df.to_csv(index=False).encode('utf-8-sig')
                st.download_button("📥 CSVダウンロード", csv, f"playlist_p{pid}.csv", "text/csv")
            
            # 画面を圧迫しないワンクリックコピーエリア
            with st.expander("📋 ワンクリックで表データをコピーする（右上のボタンをクリック）"):
                st.code(csv_df.to_csv(index=False, sep='\t'), language='csv')
            
            st.dataframe(saved_df)
