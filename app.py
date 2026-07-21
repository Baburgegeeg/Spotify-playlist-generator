import os
import json
from flask import Flask, render_template, request, redirect, session, url_for
from groq import Groq
import spotipy
from spotipy.oauth2 import SpotifyOAuth

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_secret_flask_key_123")

# Инициализация Groq
groq_api_key = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=groq_api_key) if groq_api_key else None

# Настройка OAuth Spotify
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI", "http://localhost:5000/callback")

SCOPE = "playlist-modify-public playlist-modify-private"

def create_spotify_oauth():
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE
    )

SYSTEM_INSTRUCTION = """
You are an expert music curator. 
Analyze the user's described mood/vibe and suggest EXACTLY 50 matching songs.

CRITICAL INSTRUCTION:
You MUST reply ONLY with a valid JSON array of 50 objects. Do not include markdown code blocks (like ```json), do not include any intro or outro text.

Format example:
[
  {"artist": "Artist Name", "title": "Song Title", "genre": "Genre"},
  ...
]
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    tracks = session.get('last_tracks', [])
    error = None
    user_prompt = session.get('last_prompt', "")
    playlist_url = session.pop('spotify_playlist_url', None)

    if request.method == 'POST':
        user_prompt = request.form.get('vibe', '').strip()
        
        if not user_prompt:
            error = "Пожалуйста, опишите ваше настроение."
        elif not groq_client:
            error = "API ключ Groq не настроен."
        else:
            try:
                # Запрос к Groq для генерации 50 треков
                completion = groq_client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": SYSTEM_INSTRUCTION},
                        {"role": "user", "content": f"Generate a 50-song playlist for this mood: {user_prompt}"}
                    ],
                    temperature=0.7,
                    max_tokens=4000
                )

                response_content = completion.choices[0].message.content.strip()
                
                # Очистка от маркдауна, если модель случайно добавит его
                if response_content.startswith("```"):
                    response_content = response_content.split("\n", 1)[1].rsplit("\n", 1)[0]

                raw_data = json.loads(response_content)

                if isinstance(raw_data, dict):
                    raw_data = next(iter(raw_data.values()))

                tracks = raw_data
                session['last_tracks'] = tracks
                session['last_prompt'] = user_prompt

            except Exception as e:
                error = f"Ошибка при генерации плейлиста: {str(e)}"

    return render_template('index.html', tracks=tracks, error=error, user_prompt=user_prompt, playlist_url=playlist_url)


@app.route('/export-spotify')
def export_spotify():
    """Перенаправление на авторизацию Spotify"""
    sp_oauth = create_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)


@app.route('/callback')
def callback():
    """Обработка ответа от Spotify и создание плейлиста"""
    sp_oauth = create_spotify_oauth()
    session.clear()
    code = request.args.get('code')
    token_info = sp_oauth.get_access_token(code)

    if not token_info:
        return redirect(url_for('index'))

    sp = spotipy.Spotify(auth=token_info['access_token'])
    user_id = sp.current_user()['id']

    tracks = session.get('last_tracks', [])
    prompt = session.get('last_prompt', 'AI Vibe')

    if tracks:
        # 1. Создаем плейлист
        playlist = sp.user_playlist_create(
            user=user_id,
            name=f"AI Vibe: {prompt[:30]}",
            public=True,
            description=f"Плейлист сгенерирован AI по запросу: {prompt}"
        )

        # 2. Ищем URI треков в Spotify
        track_uris = []
        for track in tracks:
            query = f"artist:{track['artist']} track:{track['title']}"
            result = sp.search(q=query, type='track', limit=1)
            items = result['tracks']['items']
            if items:
                track_uris.append(items[0]['uri'])

        # 3. Добавляем треки порциями (максимум по 100 за запрос в Spotify API)
        if track_uris:
            sp.playlist_add_items(playlist['id'], track_uris)

        session['spotify_playlist_url'] = playlist['external_urls']['spotify']

    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)
