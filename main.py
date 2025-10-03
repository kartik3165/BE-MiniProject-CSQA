from flask import Flask, render_template, request, jsonify, send_file, url_for
import cv2
import hashlib
import os
import json
import base64
from werkzeug.utils import secure_filename
from pymediainfo import MediaInfo
import threading
import time
from datetime import datetime

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SECRET_KEY'] = 'your-secret-key-here'

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Global variables for analysis status
analysis_progress = {}
video_frames = {}

ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv', 'webm'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_metadata(video_path):
    try:
        media_info = MediaInfo.parse(video_path)
        data = {}
        
        for track in media_info.tracks:
            if track.track_type == "Video":
                data['video'] = {
                    'codec': track.codec or 'N/A',
                    'duration': f"{track.duration or 'N/A'} ms",
                    'width': f"{track.width or 'N/A'} px",
                    'height': f"{track.height or 'N/A'} px",
                    'frame_rate': f"{track.frame_rate or 'N/A'} fps",
                    'bit_rate': f"{track.bit_rate or 'N/A'} bps",
                    'color_space': track.color_space or 'N/A',
                    'scan_type': track.scan_type or 'N/A'
                }
            elif track.track_type == "Audio":
                data['audio'] = {
                    'codec': track.codec or 'N/A',
                    'sample_rate': f"{track.sampling_rate or 'N/A'} Hz",
                    'channels': track.channel_s or 'N/A',
                    'bit_rate': f"{track.bit_rate or 'N/A'} bps"
                }
                
        return data if data else {'error': 'No metadata available'}
        
    except Exception as e:
        return {'error': f'Error extracting metadata: {str(e)}'}

def calculate_file_hash(path, file_id):
    try:
        hash_func = hashlib.sha256()
        file_size = os.path.getsize(path)
        processed = 0
        
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                hash_func.update(chunk)
                processed += len(chunk)
                progress = (processed / file_size) * 100
                analysis_progress[file_id]['hash_progress'] = progress
                
        hash_value = hash_func.hexdigest()
        
        result = {
            'algorithm': 'SHA-256',
            'file_size': f"{file_size:,} bytes",
            'hash': hash_value,
            'hash_breakdown': [hash_value[i:i+16] for i in range(0, len(hash_value), 16)]
        }
        
        return result
        
    except Exception as e:
        return {'error': f'Error calculating hash: {str(e)}'}

def analyze_video_frames(video_path, file_id):
    try:
        cap = cv2.VideoCapture(video_path)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        dark_frames = []
        bright_frames = []
        normal_frames = 0
        frame_number = 0
        
        analysis_limit = min(frame_count, 1000)
        
        while frame_number < analysis_limit:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ret, frame = cap.read()
            
            if not ret:
                break
                
            brightness = frame.mean()
            
            if brightness < 30:
                dark_frames.append({'frame': frame_number, 'brightness': round(brightness, 2)})
            elif brightness > 200:
                bright_frames.append({'frame': frame_number, 'brightness': round(brightness, 2)})
            else:
                normal_frames += 1
                
            frame_number += 10
            progress = (frame_number / analysis_limit) * 100
            analysis_progress[file_id]['frame_progress'] = progress
            
        cap.release()
        
        result = {
            'total_frames': f"{frame_count:,}",
            'frame_rate': f"{fps:.2f} fps",
            'duration': f"{frame_count/fps:.2f} seconds",
            'dark_frames': dark_frames[:20],  # Limit to first 20
            'bright_frames': bright_frames[:20],  # Limit to first 20
            'normal_frames': normal_frames,
            'summary': {
                'dark_count': len(dark_frames),
                'bright_count': len(bright_frames),
                'normal_count': normal_frames
            }
        }
        
        return result
        
    except Exception as e:
        return {'error': f'Error analyzing frames: {str(e)}'}

def get_video_frame(video_path, frame_number=0):
    try:
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ret, frame = cap.read()
        
        if ret:
            # Resize frame
            frame = cv2.resize(frame, (640, 480))
            _, buffer = cv2.imencode('.jpg', frame)
            frame_base64 = base64.b64encode(buffer).decode('utf-8')
            cap.release()
            return frame_base64
        
        cap.release()
        return None
        
    except Exception as e:
        return None

def perform_full_analysis(video_path, file_id):
    analysis_progress[file_id] = {
        'status': 'running',
        'metadata_done': False,
        'hash_progress': 0,
        'frame_progress': 0,
        'results': {}
    }
    
    try:
        # Extract metadata
        analysis_progress[file_id]['status'] = 'Extracting metadata...'
        metadata = extract_metadata(video_path)
        analysis_progress[file_id]['results']['metadata'] = metadata
        analysis_progress[file_id]['metadata_done'] = True
        
        # Calculate hash
        analysis_progress[file_id]['status'] = 'Calculating file hash...'
        hash_info = calculate_file_hash(video_path, file_id)
        analysis_progress[file_id]['results']['hash'] = hash_info
        
        # Analyze frames
        analysis_progress[file_id]['status'] = 'Analyzing video frames...'
        frame_analysis = analyze_video_frames(video_path, file_id)
        analysis_progress[file_id]['results']['frames'] = frame_analysis
        
        # Get first frame for preview
        first_frame = get_video_frame(video_path, 0)
        analysis_progress[file_id]['results']['preview_frame'] = first_frame
        
        analysis_progress[file_id]['status'] = 'complete'
        
    except Exception as e:
        analysis_progress[file_id]['status'] = f'error: {str(e)}'

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = str(int(time.time()))
        filename = f"{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        try:
            file.save(filepath)
            file_id = timestamp
            
            # Start analysis in background thread
            analysis_thread = threading.Thread(
                target=perform_full_analysis, 
                args=(filepath, file_id)
            )
            analysis_thread.daemon = True
            analysis_thread.start()
            
            return jsonify({
                'success': True,
                'file_id': file_id,
                'filename': file.filename
            })
            
        except Exception as e:
            return jsonify({'error': f'Failed to save file: {str(e)}'}), 500
    
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/progress/<file_id>')
def get_progress(file_id):
    if file_id not in analysis_progress:
        return jsonify({'error': 'File not found'}), 404
    
    return jsonify(analysis_progress[file_id])

@app.route('/results/<file_id>')
def get_results(file_id):
    if file_id not in analysis_progress:
        return jsonify({'error': 'File not found'}), 404
    
    if analysis_progress[file_id]['status'] == 'complete':
        return jsonify(analysis_progress[file_id]['results'])
    
    return jsonify({'status': 'not_ready'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)