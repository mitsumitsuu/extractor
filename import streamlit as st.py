import streamlit as st
import pandas as pd
import re
import requests
import json
import urllib.parse
import urllib.request
from googleapiclient.discovery import build
from io import BytesIO
import pytesseract
from PIL import Image

# ==========================================
# 1. 初期設定とデフォルト辞書
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"
PLAYLIST_NG_WORDS = "short, 歌ってみた, 踊ってみた, cover, カバー, inst, off vocal, オフボーカル, カラオケ, 実況, 弾いてみた"

st.set_page_config(page_title="楽曲抽出＆特定システム", layout="wide")

# ==========================================
# 2. タイトルと初心者向けガイド
# ==========================================
col_title, col_link = st.columns([4, 1])
with col_title:
    st.title("🎶 楽曲抽出＆特定システム")
with col_link:
    st.write("") # スペース調整
    st.write("")
    st.markdown("[👤 制作者 (Mitsu) の lit.link](https://lit.link/_mitsu_3_)")

st.markdown("""
**はじめての方へ：このツールでできること**
このシステムは、音楽のプレイリスト整理や、わからない楽曲名の特定を自動化するお助けツールです。
上から順番に項目を埋めていくだけで、簡単に操作できます。

*   **🔗 URLから一括抽出:** YouTubeやニコニコなどのURLから、曲名と合成音声名をリストアップしExcel出力します。
*   **🖼️ 画像・ローマ字から楽曲特定:** スクショやローマ字から、正しい日本語の曲名を探し出します。
*   **📁 Excelからプレイリスト生成:** 曲名のリストから、ノイズを排除した即席のYouTubeプレイリストURLを作ります。
---
""")

# ==========================================
# 3. データ処理ロジック（関数群）
# ==========================================
def extract_vocals(title, description, keywords, ng_list):
    found_vocals = set()
    title_str = str(title) if title else ""
    desc_str = str(description) if description else ""
    for kw in keywords:
        if kw in title_str: found_vocals.add(kw)
    if not any(ng in desc_str for ng in ng_list):
        for kw in keywords:
            if kw in desc_str: found_vocals.add(kw)
    return " / ".join(list(found_vocals))

def clean_title(raw_title):
    title = str(raw_title)
    title = re.sub(r"(?i)[\(（\[【].*?(remix|bootleg|edit|mashup|flip|vip|cover).*?[\)）\]】]", "", title)
    title = re.sub(r"【.*?】|\[.*?\]", "", title)
    title = re.split(r"(?i)\s+feat\.\s+|\s+ft\.\s+", title)[0]
    title = re.split(r"\s+/\s+|\s+-\s+", title)[0]
    return title.strip()

def get_youtube_playlist(api_key, url):
    match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
    if not match: raise ValueError("有効なYouTubeプレイリストIDが見つかりません。")
    youtube = build("youtube", "v3", developerKey=api_key)
    videos, next_page_token = [], None
    while True:
        request = youtube.playlistItems().list(part="snippet", playlistId=match.group(1), maxResults=50, pageToken=next_page_token)
        response = request.execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            title = snippet["title"]
            if title in ["Private video", "Deleted video"]: continue
            videos.append({"曲名": title, "概要欄データ": snippet.get("description", ""), "URL": f"https://www.youtube.com/watch?v={snippet['resourceId']['videoId']}"})
        next_page_token = response.get("nextPageToken")
        if not next_page_token: break
    return videos

def get_youtube_playlist_no_api(url):
    match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
    if match:
        # 動画ページに付属しているプレイリストURLなどを、純粋なプレイリスト専用URLに変換する
        playlist_id = match.group(1)
        clean_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        req = urllib.request.Request(clean_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "ja-JP,ja;q=0.9"})
        try:
            with urllib.request.urlopen(req) as response:
                html = response.read().decode('utf-8')
                match_data = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
                if not match_data:
                    raise ValueError("プレイリストデータの解析に失敗しました。")
                data = json.loads(match_data.group(1))
                
                videos = []
                def find_playlist_videos(node):
                    if isinstance(node, list):
                        for i in node:
                            for x in find_playlist_videos(i): yield x
                    elif isinstance(node, dict):
                        if 'playlistVideoRenderer' in node: yield node['playlistVideoRenderer']
                        for j in node.values():
                            for x in find_playlist_videos(j): yield x
                
                for item in find_playlist_videos(data):
                    vid = item.get('videoId')
                    title = "".join([run.get('text', '') for run in item.get('title', {}).get('runs', [])])
                    desc = "".join([run.get('text', '') for run in item.get('descriptionSnippet', {}).get('runs', [])]) if 'descriptionSnippet' in item else ""
                    if vid and title:
                        videos.append({
                            "曲名": title,
                            "概要欄データ": desc,
                            "URL": f"https://www.youtube.com/watch?v={vid}"
                        })
                if not videos:
                    raise ValueError("プレイリスト内に動画が見つかりませんでした。")
                return videos
        except Exception as e:
            raise ValueError(f"{e}")
    else:
        vid = extract_youtube_id(url)
        if vid:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ja-JP,ja;q=0.9"})
            try:
                with urllib.request.urlopen(req) as response:
                    html = response.read().decode('utf-8')
                    match_title = re.search(r"<title>(.*?)</title>", html)
                    title = match_title.group(1).replace(" - YouTube", "") if match_title else "YouTube Video"
                    return [{"曲名": title, "概要欄データ": "", "URL": f"https://www.youtube.com/watch?v={vid}"}]
            except Exception:
                return [{"曲名": "YouTube Video", "概要欄データ": "", "URL": f"https://www.youtube.com/watch?v={vid}"}]
        else:
            raise ValueError("有効なYouTube URLが見つかりませんでした。")

def get_niconico_playlist(url):
    match = re.search(r"mylist/(\d+)", url)
    if not match: raise ValueError("有効なニコニコ動画のマイリストURLが見つかりません。")
    api_url = f"https://nvapi.nicovideo.jp/v2/mylists/{match.group(1)}"
    res = requests.get(api_url, headers={"X-Frontend-Id": "6", "X-Frontend-Version": "0"})
    res.encoding = 'utf-8'
    if res.status_code != 200: raise ValueError("ニコニコ動画のリストが読み込めませんでした。")
    items = res.json().get("data", {}).get("mylist", {}).get("items", [])
    videos = []
    for item in items:
        video = item.get("video", {})
        if video: videos.append({"曲名": video.get("title", "Unknown"), "概要欄データ": video.get("shortDescription", ""), "URL": f"https://www.nicovideo.jp/watch/{video.get('id', '')}"})
    return videos

def get_soundcloud_data(url):
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    res.encoding = 'utf-8'
    match = re.search(r"window\.__sc_hydration = (\[.*?\]);\s*</script>", res.text)
    if not match: raise ValueError("楽曲データが見つかりませんでした。")
    hydration_data = json.loads(match.group(1))
    videos = []
    for item in hydration_data:
        if item.get("hydratable") in ["playlist", "sound"]:
            tracks = item.get("data", {}).get("tracks", []) if item.get("hydratable") == "playlist" else [item.get("data", {})]
            for t in tracks:
                if isinstance(t, dict) and t.get("title"):
                    videos.append({"曲名": t.get("title"), "概要欄データ": f"{t.get('user', {}).get('username', '')} / {t.get('description') or ''}", "URL": t.get("permalink_url", "")})
            if videos: return videos
    return videos

def search_vocadb(query_text):
    url = "https://vocadb.net/api/songs"
    params = {"query": query_text, "maxResults": 1, "sort": "FavoritedTimes", "fields": "Names"}
    try:
        response = requests.get(url, params=params, headers={"Accept": "application/json"})
        if response.status_code == 200 and response.json().get("items"):
            return response.json()["items"][0]["defaultName"]
    except Exception as e:
        return f"検索エラー: {e}"
    return None

def extract_text_from_image(image_file):
    return pytesseract.image_to_string(Image.open(image_file), lang='eng+jpn').strip()

def search_youtube_no_api_advanced(query, ng_words_list):
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            match = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
            if not match: return None
            data = json.loads(match.group(1))

            def find_videos(node):
                if isinstance(node, list):
                    for i in node:
                        for x in find_videos(i): yield x
                elif isinstance(node, dict):
                    if 'videoRenderer' in node: yield node['videoRenderer']
                    for j in node.values():
                        for x in find_videos(j): yield x

            for video in find_videos(data):
                vid = video.get('videoId')
                title = "".join([run.get('text', '') for run in video.get('title', {}).get('runs', [])])
                length_text = video.get('lengthText', {}).get('simpleText', '')
                
                if not vid or not length_text: continue

                parts = length_text.split(':')
                sec = 0
                if len(parts) == 2: sec = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3: sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                
                if sec < 80 or sec > 420: continue
                
                title_lower = title.lower()
                is_ng = any(ng.lower() in title_lower for ng in ng_words_list)
                if is_ng: continue
                
                return vid
    except Exception:
        pass
    return None

def extract_youtube_id(url_text):
    url_str = str(url_text)
    match = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([a-zA-Z0-9_-]{11})", url_str)
    if match: return match.group(1)
    return None

def extract_any_url(row_data):
    for item in row_data:
        cell_str = str(item)
        match = re.search(r"(https?://[^\s]+)", cell_str)
        if match:
            return match.group(1)
    return None

# ==========================================
# 4. メイン画面（タブ構造）
# ==========================================
tab1, tab2, tab3 = st.tabs(["🔗 URLから一括抽出", "🖼️ 画像から特定", "📁 プレイリスト生成"])

# --- タブ1: 従来のプレイリスト抽出機能 ---
with tab1:
    st.header("⚙️ 1. システム設定＆抽出モード")
    
    with st.expander("🔑 YouTube API Key の取得方法（クリックで表示）"):
        st.markdown("""
        **【API Key 取得手順】**
        1. **Google Cloud Console** ([console.cloud.google.com](https://console.cloud.google.com/)) にGoogleアカウントでログインします。
        2. 画面上部から新しいプロジェクトを作成します（名前は任意）。
        3. **「APIとサービス」 > 「ライブラリ」** を開き、**「YouTube Data API v3」** を検索して「有効にする」をクリックします。
        4. **「APIとサービス」 > 「認証情報」** を開き、上部の **「＋ 認証情報を作成」 > 「APIキー」** を選択します。
        5. 生成された文字列（`AIzaSy...`から始まるコード）をコピーし、下の入力欄に貼り付けます。
        """)
    
    extraction_mode = st.radio(
        "YouTube抽出モードの選択",
        ["🔑 APIあり（推奨・全件高精度抽出）", "⚡ APIなし（簡易抽出・100曲程度まで）"],
        horizontal=True
    )
    
    youtube_api_key = ""
    if "APIあり" in extraction_mode:
        youtube_api_key = st.text_input("🔑 YouTube API Key を入力してください", type="password")
    else:
        st.caption("※「APIなし」モードでは、APIキーの設定は不要ですが、取得上限が100曲程度に制限され、概要欄データの判定精度が低くなる場合があります。")

    col1, col2 = st.columns(2)
    with col1:
        target_keywords = [k.strip() for k in st.text_area("🔍 抽出するワード（歌手・合成音声名など）", DEFAULT_KEYWORDS, height=100).split(",") if k.strip()]
    with col2:
        ng_words = [n.strip() for n in st.text_area("🚫 除外（NG）ワード", DEFAULT_NG_WORDS, height=100).split(",") if n.strip()]

    st.markdown("---")
    st.header("🔍 2. プレイリスト・楽曲URLの解析")
    playlist_url = st.text_input("URLを入力（YouTube / ニコニコ動画 / SoundCloud）")

    if st.button("一括抽出を開始する", type="primary"):
        if not playlist_url:
            st.warning("⚠️ URLを入力してください。")
        else:
            with st.spinner("データを取得・解析中..."):
                try:
                    raw_data = []
                    if "youtube.com" in playlist_url or "youtu.be" in playlist_url:
                        if "APIあり" in extraction_mode:
                            if not youtube_api_key:
                                raise ValueError("「APIあり」モードが選択されています。API Keyを入力するか、「APIなし」モードに切り替えてください。")
                            raw_data = get_youtube_playlist(youtube_api_key, playlist_url)
                        else:
                            raw_data = get_youtube_playlist_no_api(playlist_url)
                    elif "nicovideo.jp" in playlist_url:
                        raw_data = get_niconico_playlist(playlist_url)
                    elif "soundcloud.com" in playlist_url:
                        raw_data = get_soundcloud_data(playlist_url)
                    else:
                        raise ValueError("対応していないURLです。")

                    results = [{"曲名": clean_title(item["曲名"]), "合成音声": extract_vocals(item["曲名"], item["概要欄データ"], target_keywords, ng_words), "URL": item["URL"]} for item in raw_data]
                    df = pd.DataFrame(results)
                    
                    if df.empty:
                        st.warning("対象となる楽曲が見つかりませんでした。")
                    else:
                        st.success(f"✅ {len(df)}曲の解析が完了しました！")
                        st.dataframe(df, use_container_width=True)
                        output = BytesIO()
                        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                            df.to_excel(writer, index=False, sheet_name='Playlist Data')
                        st.download_button("📥 Excelファイルとしてダウンロード", data=output.getvalue(), file_name="playlist_result.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                except Exception as e:
                    st.error(f"❌ エラーが発生しました: APIなしでの取得に失敗しました: {e}")

# --- タブ2: 画像認識 (OCR) & VocaDB検索 ---
with tab2:
    st.header("🖼️ 画像認識・ローマ字から楽曲特定")
    uploaded_file = st.file_uploader("楽曲名が写っている画像（スクショなど）をアップロード", type=["png", "jpg", "jpeg"])
    manual_query = st.text_input("または、検索したい文字列（ローマ字など）を直接入力")
    
    if st.button("楽曲を特定する", type="primary"):
        query_text = ""
        if uploaded_file is not None:
            with st.spinner("画像を解析中..."):
                st.image(uploaded_file, caption="アップロードされた画像", width=300)
                extracted_text = extract_text_from_image(uploaded_file)
                st.write("**画像から抽出されたテキスト:**"); st.code(extracted_text)
                query_text = extracted_text.replace("\n", " ").strip()
        if manual_query: query_text = manual_query.strip()
            
        if query_text:
            with st.spinner("VocaDBデータベースを検索中..."):
                official_title = search_vocadb(query_text)
                if official_title: st.success(f"🎉 特定成功！ 正式な曲名: **{official_title}**")
                else: st.warning("VocaDBに一致する楽曲が見つかりませんでした。")
        else: st.warning("画像を入れるか、検索キーワードを入力してください。")

# --- タブ3: Excelからプレイリスト生成 (API不要版) ---
with tab3:
    st.header("📁 Excelからプレイリスト生成 (API不要版)")
    st.markdown("アップロードしたExcelファイルのURLリストから、即席のYouTubeプレイリストURLを生成します。\n\n※YouTube以外のリンク（ニコニコ動画など）が含まれている場合は、下に個別のアクセスリンクとしてまとめられます。")
    
    strict_mode = st.checkbox("✅ 完全一致モード（列名に関わらず、YouTubeのURLが入力されている楽曲のみを抽出し、曖昧な検索補完を行わない）", value=True)
    
    playlist_ng_words_input = st.text_area("🚫 検索時の除外ワード（※完全一致モードをオフにした場合のみ機能します）", PLAYLIST_NG_WORDS, height=100)
    pl_ng_words = [n.strip() for n in playlist_ng_words_input.split(",") if n.strip()]
    
    uploaded_excel = st.file_uploader("楽曲リスト（Excelファイル）をアップロード", type=["xlsx"])
    
    if st.button("プレイリストURLを生成する", type="primary"):
        if uploaded_excel is not None:
            with st.spinner("プレイリストを構築中..."):
                try:
                    df = pd.read_excel(uploaded_excel)
                    video_ids = []
                    other_platforms = []
                    skipped_count = 0
                    searched_warnings = []
                    
                    progress_bar = st.progress(0)
                    total_rows = len(df)
                    
                    for index, row in df.iterrows():
                        vid = None
                        found_url = extract_any_url(row.values)
                        track_number = index + 1
                        col_name = "曲名" if "曲名" in df.columns else (df.columns[0] if len(df.columns) > 0 else "不明")
                        song_title = str(row.get(col_name, f"不明な曲（{track_number}行目）"))
                        
                        if found_url:
                            vid = extract_youtube_id(found_url)
                            if not vid:
                                other_platforms.append({"title": song_title, "url": found_url})
                        
                        if not vid:
                            if strict_mode:
                                if not found_url:
                                    skipped_count += 1
                            else:
                                if song_title and song_title != "nan" and not found_url:
                                    vid = search_youtube_no_api_advanced(song_title, pl_ng_words)
                                    if vid:
                                        searched_warnings.append(f"・{track_number}曲目：{song_title}")
                                    else:
                                        skipped_count += 1
                        
                        if vid:
                            video_ids.append(vid)
                            
                        progress_bar.progress((index + 1) / total_rows)
                            
                    if video_ids:
                        st.success(f"✅ {len(video_ids)}曲のYouTube動画データを結合しました！")
                        
                        if skipped_count > 0:
                            st.info(f"ℹ️ URLが記載されていない等の理由により、{skipped_count}曲をスキップしました。")
                        
                        if searched_warnings:
                            st.warning("⚠️ 以下の楽曲はURLリンクが無かったため、タイトル検索で自動補完しました。")
                            with st.expander("検索で補完した楽曲の一覧を確認する"):
                                for warning in searched_warnings:
                                    st.write(warning)
                        
                        chunked_ids = [video_ids[i:i + 50] for i in range(0, len(video_ids), 50)]
                        
                        for idx, chunk in enumerate(chunked_ids):
                            playlist_url = f"https://www.youtube.com/watch_videos?video_ids={','.join(chunk)}"
                            st.markdown(f"**🎧 プレイリスト Part {idx+1} (最大50曲):**\n[ここをクリックして連続再生を開始する]({playlist_url})")
                            st.code(playlist_url)
                    else:
                        st.error("有効なYouTube動画リンクが一つも見つかりませんでした。")
                    
                    if other_platforms:
                        st.markdown("---")
                        st.subheader("🌐 その他のプラットフォームの楽曲")
                        st.markdown("ニコニコ動画やSoundCloudなど、YouTube以外のリンクが設定されていた楽曲です。以下のボタンから直接サイトへアクセスできます。")
                        
                        for item in other_platforms:
                            st.markdown(f"- **{item['title']}** : [リンクを開く]({item['url']})")
                            
                except Exception as e:
                    st.error(f"❌ エラーが発生しました: {e}")
        else:
            st.warning("Excelファイルをアップロードしてください。")

# ==========================================
# 5. お問い合わせ・ご要望フォーム
# ==========================================
st.markdown("---")
st.header("✉️ お問い合わせ / ご要望")
st.markdown("システムの不具合や機能追加のご要望などがございましたら、以下のフォームから管理者に送信できます。")

with st.form("contact_form"):
    subject_input = st.text_input("件名", placeholder="例：APIなし抽出のエラーについて")
    body_input = st.text_area("お問い合わせ内容", placeholder="発生した問題やご要望を詳細にご記入ください。", height=150)
    
    submitted = st.form_submit_button("管理者に送信する")
    if submitted:
        if not subject_input or not body_input:
            st.warning("件名とお問い合わせ内容の両方を入力してください。")
        else:
            try:
                # FormSubmitのAJAXエンドポイントを使用してメールを送信
                res = requests.post("https://formsubmit.co/ajax/yukimitsuyamamura0315@gmail.com", data={
                    "件名": subject_input,
                    "メッセージ": body_input,
                    "_subject": f"【楽曲抽出システム】お問い合わせ: {subject_input}"
                })
                if res.status_code == 200:
                    st.success("✅ 送信が完了しました。貴重なご意見ありがとうございます！")
                else:
                    st.error("送信に失敗しました。しばらく時間をおいてから再度お試しください。")
            except Exception as e:
                st.error(f"通信エラーが発生しました: {e}")
