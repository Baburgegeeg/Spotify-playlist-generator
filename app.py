import os
import json
import random
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from groq import Groq
import spotipy
from spotipy.oauth2 import SpotifyOAuth

app = Flask(__name__)

# Секретный ключ для сессий Flask (на Render задайте FLASK_SECRET_KEY в Environment Variables)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_secret_flask_key_123_change_in_prod")

# Настройки безопасности cookie для работы через HTTPS (Render)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Инициализация Groq API
groq_api_key = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=groq_api_key) if groq_api_key else None

# Настройки Spotify OAuth
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:5000/callback")

# Scope с разрешениями для создания публичных и приватных плейлистов
SCOPE = "playlist-modify-public playlist-modify-private"

def create_spotify_oauth():
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE,
        cache_handler=None
    )

SYSTEM_INSTRUCTION = """
You are an expert music curator. 
Suggest AT LEAST 40 tracks for the given mood/genre.

CRITICAL INSTRUCTIONS:
1. Return a minimum of 40 songs.
2. Output ONLY a raw JSON array of objects with keys "artist" and "title".
3. No markdown formatting, no code blocks, no preamble.

Example:
[{"artist": "Band", "title": "Song"}]
"""

def generate_tracks(user_prompt):
    random_seed = random.randint(1, 100000)
    
    completion = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": f"Generate 40+ songs for mood: '{user_prompt}'. Seed: {random_seed}"}
        ],
        temperature=0.85,
        max_tokens=3500
    )

    response_content = completion.choices[0].message.content.strip()
    
    if response_content.startswith("```"):
        response_content = response_content.split("\n", 1)[1].rsplit("\n", 1)[0]

    raw_data = json.loads(response_content)

    if isinstance(raw_data, dict):
        raw_data = next(iter(raw_data.values()))

    return raw_data


def search_single_track(sp, track):
    """Параллельный поиск одного трека через Spotify API"""
    try:
        query = f"artist:{track['artist']} track:{track['title']}"
        result = sp.search(q=query, type='track', limit=1)
        items = result['tracks']['items']
        if items:
            return items[0]['uri']
    except Exception:
        pass
    return None


@app.route('/', methods=['GET', 'POST'])
def index():
    tracks = session.get('last_tracks', [])
    error = session.pop('last_error', None)
    user_prompt = session.get('last_prompt', "")

    if request.method == 'POST':
        user_prompt = request.form.get('vibe', '').strip()
        
        if not user_prompt:
            error = "Please enter a mood or prompt."
        elif not groq_client:
            error = "Groq API key is missing."
        else:
            try:
                tracks = generate_tracks(user_prompt)
                session['last_tracks'] = tracks
                session['last_prompt'] = user_prompt
            except Exception as e:
                error = f"Error generating playlist: {str(e)}"

    return render_template('index.html', tracks=tracks, error=error, user_prompt=user_prompt)


@app.route('/retry', methods=['POST'])
def retry():
    user_prompt = session.get('last_prompt', "")
    if not user_prompt:
        return redirect(url_for('index'))
    
    if not groq_client:
        session['last_error'] = "Groq API key is missing."
        return redirect(url_for('index'))

    try:
        tracks = generate_tracks(user_prompt)
        session['last_tracks'] = tracks
    except Exception as e:
        session['last_error'] = f"Error regenerating playlist: {str(e)}"

    return redirect(url_for('index'))


@app.route('/login-spotify')
def login_spotify():
    """Сбрасывает прошлую сессию и перенаправляет на авторизацию Spotify"""
    session.pop('spotify_token_info', None)
    sp_oauth = create_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)


@app.route('/callback')
def callback():
    """Сохраняет полученный token_info в сессию Flask"""
    sp_oauth = create_spotify_oauth()
    code = request.args.get('code')
    
    if code:
        try:
            token_info = sp_oauth.get_access_token(code, check_cache=False)
            session['spotify_token_info'] = token_info
        except Exception as e:
            session['last_error'] = f"Spotify Auth Error: {str(e)}"

    return redirect(url_for('index'))


@app.route('/api/export-spotify', methods=['POST'])
def export_spotify_api():
    """Фоновый API-эндпоинт экспорта плейлиста в Spotify"""
    token_info = session.get('spotify_token_info')
    
    if not token_info:
        return jsonify({"success": False, "error": "Not authenticated with Spotify. Please click 'Connect Spotify' first."}), 401

    sp_oauth = create_spotify_oauth()
    
    # Автоматическое обновление просроченного токена
    if sp_oauth.is_token_expired(token_info):
        try:
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            session['spotify_token_info'] = token_info
        except Exception as e:
            return jsonify({"success": False, "error": "Session expired. Please reconnect Spotify."}), 401

    access_token = token_info.get('access_token')

    data = request.get_json() or {}
    tracks = data.get('tracks', [])
    prompt = data.get('prompt', 'AI Vibe')

    if not tracks:
        return jsonify({"success": False, "error": "No tracks to export"}), 400

    try:
        sp = spotipy.Spotify(auth=access_token)

        # Создаем плейлист текущему пользователю (универсальный метод для избежания 403)
        playlist = sp.current_user_playlist_create(
            name=f"AI Vibe: {prompt[:30]}",
            public=True,
            description=f"Generated playlist for prompt: {prompt}"
        )

        # Многопоточный поиск треков (10 потоков)
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(search_single_track, sp, track) for track in tracks]
            track_uris = [f.result() for f in futures if f.result() is not None]

        # Добавление найденных треков
        if track_uris:
            for i in range(0, len(track_uris), 100):
                sp.playlist_add_items(playlist['id'], track_uris[i:i + 100])

        return jsonify({
            "success": True, 
            "playlist_url": playlist['external_urls']['spotify'],
            "found_count": len(track_uris)
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
