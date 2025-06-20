import azure.cognitiveservices.speech as speechsdk
import requests
import traceback
import time
import os
import logging
from config import (
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT
)
import openai
from flask_socketio import SocketIO
import json

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Configure OpenAI client
openai.api_type = "azure"
openai.api_base = AZURE_OPENAI_ENDPOINT
openai.api_version = AZURE_OPENAI_API_VERSION
openai.api_key = AZURE_OPENAI_API_KEY

class MeetingTranscriber:
    def __init__(self, socketio=None):
        """Initialize the transcriber with Azure Speech Services configuration."""
        try:
            # Set environment variables for audio
            os.environ['PULSE_SERVER'] = 'unix:/tmp/pulse/native'
            os.environ['PULSE_COOKIE'] = '/tmp/pulse/cookie'
            os.environ['PULSE_CLIENTCONFIG'] = '/tmp/pulse/client.conf'
            
            # Ensure PulseAudio directories exist
            pulse_dir = '/tmp/pulse'
            if not os.path.exists(pulse_dir):
                os.makedirs(pulse_dir, mode=0o777)
            
            # Create PulseAudio client config if it doesn't exist
            client_conf = '/tmp/pulse/client.conf'
            if not os.path.exists(client_conf):
                with open(client_conf, 'w') as f:
                    f.write("""
default-server = unix:/tmp/pulse/native
autospawn = no
daemon-binary = /bin/true
enable-shm = false
""")
                os.chmod(client_conf, 0o644)
            
            logger.info("Initializing speech configuration...")
            self.speech_config = speechsdk.SpeechConfig(
                subscription=AZURE_SPEECH_KEY,
                region=AZURE_SPEECH_REGION
            )
            self.speech_config.speech_recognition_language = "en-US"
            
            # Configure speech recognition settings
            self.speech_config.set_property(
                speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs,
                "5000"
            )
            self.speech_config.set_property(
                speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs,
                "5000"
            )
            
            # Enable word-level timestamps for better speaker tracking
            self.speech_config.set_property(
                speechsdk.PropertyId.SpeechServiceResponse_RequestWordLevelTimestamps,
                "true"
            )
            
            # Enable detailed results
            self.speech_config.set_property(
                speechsdk.PropertyId.SpeechServiceResponse_RequestDetailedResultTrueFalse,
                "true"
            )
            
            # Configure audio with fallback options
            try:
                logger.info("Attempting to use default microphone...")
                self.audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
            except Exception as e:
                logger.warning(f"Failed to use default microphone: {str(e)}")
                try:
                    logger.info("Attempting to use default audio input...")
                    self.audio_config = speechsdk.audio.AudioConfig()
                except Exception as e:
                    logger.error(f"Failed to configure audio: {str(e)}")
                    raise
            
            self.transcript = []
            self.speaker_transcript = []  # Store speaker-specific transcript
            self.socketio = socketio
            self.recognizer = None
            self.current_speaker = None
            self.speaker_count = 0
            self.last_speaker_time = time.time()
            
        except Exception as e:
            logger.error(f"Error initializing transcriber: {str(e)}")
            raise

    def handle_result(self, evt):
        """Handle speech recognition results with speaker identification"""
        try:
            result = evt.result
            text = result.text
            
            # Simple speaker tracking based on silence duration
            current_time = time.time()
            if current_time - self.last_speaker_time > 2.0:  # If more than 2 seconds of silence
                self.speaker_count = (self.speaker_count + 1) % 4  # Cycle through 4 speakers
                self.current_speaker = f"Speaker {self.speaker_count + 1}"
            
            self.last_speaker_time = current_time
            
            # Create transcript entry with speaker information
            transcript_entry = {
                'text': text,
                'speaker': self.current_speaker or "Speaker 1",
                'timestamp': time.strftime('%H:%M:%S'),
                'speaker_id': self.speaker_count + 1
            }
            
            self.transcript.append(text)
            self.speaker_transcript.append(transcript_entry)
            
            # Emit the transcript update through Socket.IO with speaker information
            if self.socketio:
                self.socketio.emit('transcript_update', transcript_entry)
                print(f"Emitted transcript update: {json.dumps(transcript_entry)}")
                
        except Exception as e:
            print(f"Error in handle_result: {str(e)}")
            import traceback
            traceback.print_exc()

    def handle_canceled(self, evt):
        """Handle speech recognition cancellation"""
        try:
            print(f"Speech recognition canceled: {evt.result.text}")
            print(f"Reason: {evt.result.reason}")
            if evt.result.reason == speechsdk.CancellationReason.Error:
                print(f"Error details: {evt.result.error_details}")
        except Exception as e:
            print(f"Error in handle_canceled: {str(e)}")
            import traceback
            traceback.print_exc()

    def handle_session_started(self, evt):
        """Handle speech recognition session start"""
        try:
            print(f"Speech recognition session started: {evt.session_id}")
        except Exception as e:
            print(f"Error in handle_session_started: {str(e)}")
            import traceback
            traceback.print_exc()

    def handle_session_stopped(self, evt):
        """Handle speech recognition session stop"""
        try:
            print(f"Speech recognition session stopped: {evt.session_id}")
        except Exception as e:
            print(f"Error in handle_session_stopped: {str(e)}")
            import traceback
            traceback.print_exc()

    def start_recording(self):
        """Start the speech recognition session"""
        try:
            logger.info("Starting recording...")
            logger.info("Configuring audio input...")
            
            # Create speech recognizer with error handling
            try:
                self.recognizer = speechsdk.SpeechRecognizer(
                    speech_config=self.speech_config,
                    audio_config=self.audio_config
                )
            except Exception as e:
                logger.error(f"Error creating speech recognizer: {str(e)}")
                # Try alternative configuration
                logger.info("Attempting alternative audio configuration...")
                self.audio_config = speechsdk.audio.AudioConfig()
                self.recognizer = speechsdk.SpeechRecognizer(
                    speech_config=self.speech_config,
                    audio_config=self.audio_config
                )
            
            # Connect event handlers
            logger.info("Connecting event handlers...")
            self.recognizer.recognized.connect(self.handle_result)
            self.recognizer.canceled.connect(self.handle_canceled)
            self.recognizer.session_started.connect(self.handle_session_started)
            self.recognizer.session_stopped.connect(self.handle_session_stopped)
            
            logger.info("Starting continuous recognition...")
            self.recognizer.start_continuous_recognition()
            logger.info("Recording started successfully")
        except Exception as e:
            logger.error(f"Error starting recording: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    def stop_recording(self):
        """Stop recording and return the transcript with speaker information."""
        try:
            if self.recognizer:
                print("Stopping continuous recognition...")
                self.recognizer.stop_continuous_recognition()
                
                # Format the transcript with speaker information
                formatted_transcript = []
                for entry in self.speaker_transcript:
                    formatted_transcript.append(
                        f"[{entry['timestamp']}] {entry['speaker']}: {entry['text']}"
                    )
                
                full_transcript = "\n".join(formatted_transcript)
                print(f"Full transcript with speakers: {full_transcript}")
                return full_transcript
            return ""
        except Exception as e:
            print(f"Error stopping recording: {str(e)}")
            import traceback
            traceback.print_exc()
            return ""

    def generate_summary(self, transcript=None):
        """Generate a summary of the transcript using Azure OpenAI with speaker-specific action items."""
        try:
            if not transcript:
                # Format the transcript with speaker information for better context
                formatted_transcript = []
                for entry in self.speaker_transcript:
                    formatted_transcript.append(
                        f"[{entry['timestamp']}] {entry['speaker']}: {entry['text']}"
                    )
                transcript = "\n".join(formatted_transcript)
            
            if not transcript:
                return "No transcript available to summarize."
            
            print("Generating summary using Azure OpenAI...")
            print(f"Using deployment: {AZURE_OPENAI_DEPLOYMENT}")
            print(f"Using endpoint: {AZURE_OPENAI_ENDPOINT}")
            print(f"Transcript length: {len(transcript)} characters")
            
            response = openai.ChatCompletion.create(
                model=AZURE_OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": """You are a helpful assistant that summarizes meeting transcripts. 
                    Your response should be structured in three parts:
                    1. A concise summary of the main points discussed
                    2. A list of action items, organized by speaker
                    3. A general list of action items that aren't speaker-specific
                    
                    For speaker-specific action items, use the format:
                    [Speaker Name]'s Action Items:
                    - Item 1
                    - Item 2
                    
                    For general action items, use the format:
                    General Action Items:
                    - Item 1
                    - Item 2"""},
                    {"role": "user", "content": f"""Please provide a summary and action items for this meeting transcript:

{transcript}

Please structure your response as follows:
1. First, provide a concise summary of the main points discussed
2. Then, list all action items organized by speaker (if any speaker-specific items are identified)
3. Finally, list any general action items that aren't specific to a particular speaker
4. Format all action items as bulleted lists"""}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            
            summary = response.choices[0].message.content
            print(f"Generated summary: {summary[:200]}...")
            return summary
        except Exception as e:
            print(f"Error generating summary: {str(e)}")
            import traceback
            traceback.print_exc()
            return f"Error generating summary: {str(e)}" 