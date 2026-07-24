import os
import json
import random
from flask import Flask, render_template, request, redirect, session, url_for
from groq import Groq
import spotipy
from spotipy.oauth2 import SpotifyOAuth

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_secret_flask_key_123")

# Initialize Groq
groq_api_key = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=groq_api_key) if groq_api_key else None

# Spotify OAuth Configuration
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:5000/callback")

SCOPE = "playlist-modify-public playlist-modify-private"

def create_spotify_oauth():
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE
    )

# Убрали лишние поля (genre), чтобы экономить токены и не упираться в TPM
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
    """Helper function to call Groq API without hitting TPM limits"""
    random_seed = random.randint(1, 100000)
    
    completion = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": f"Generate 40+ songs for mood: '{user_prompt}'. Seed: {random_seed}"}
        ],
        temperature=0.85,
        max_tokens=3500  # Снижено с 6000, чтобы уложиться в бесплатный TPM лимит Groq
    )

    response_content = completion.choices[0].message.content.strip()
    
    if response_content.startswith("```"):
        response_content = response_content.split("\n", 1)[1].rsplit("\n", 1)[0]

    raw_data = json.loads(response_content)

    if isinstance(raw_data, dict):
        raw_data = next(iter(raw_data.values()))

    return raw_data


@app.route('/', methods=['GET', 'POST'])
def index():
    tracks = session.get('last_tracks', [])
    error = None
    user_prompt = session.get('last_prompt', "")
    playlist_url = session.pop('spotify_playlist_url', None)

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

    return render_template('index.html', tracks=tracks, error=error, user_prompt=user_prompt, playlist_url=playlist_url)


@app.route('/retry', methods=['POST'])
def retry():
    """Regenerates tracks for the existing prompt"""
    user_prompt = session.get('last_prompt', "")
    if not user_prompt:
        return redirect(url_for('index'))
    
    if not groq_client:
        return render_template('index.html', tracks=[], error="Groq API key is missing.", user_prompt=user_prompt)

    try:
        tracks = generate_tracks(user_prompt)
        session['last_tracks'] = tracks
    except Exception as e:
        return render_template('index.html', tracks=session.get('last_tracks', []), error=f"Error regenerating playlist: {str(e)}", user_prompt=user_prompt)

    return redirect(url_for('index'))


@app.route('/export-spotify')
def export_spotify():
    sp_oauth = create_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)


@app.route('/callback')
def callback():
    sp_oauth = create_spotify_oauth()
    code = request.args.get('code')
    token_info = sp_oauth.get_access_token(code)

    if not token_info:
        return redirect(url_for('index'))

    sp = spotipy.Spotify(auth=token_info['access_token'])
    user_id = sp.current_user()['id']

    tracks = session.get('last_tracks', [])
    prompt = session.get('last_prompt', 'AI Vibe')

    if tracks:
        playlist = sp.user_playlist_create(
            user=user_id,
            name=f"AI Vibe: {prompt[:30]}",
            public=True,
            description=f"Generated playlist for prompt: {prompt}"
        )

        track_uris = []
        for track in tracks:
            query = f"artist:{track['artist']} track:{track['title']}"
            result = sp.search(q=query, type='track', limit=1)
            items = result['tracks']['items']
            if items:
                track_uris.append(items[0]['uri'])

        if track_uris:
            sp.playlist_add_items(playlist['id'], track_uris)

        session['spotify_playlist_url'] = playlist['external_urls']['spotify']

    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)

