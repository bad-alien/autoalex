import os
import logging
import subprocess
import shutil

logger = logging.getLogger("Autoalex.RemixService")

VALID_STEMS = ["bass", "drums", "vocals", "other"]
DEFAULT_GAIN_DB = 4
MAX_GAIN_DB = 100


class RemixService:
    def __init__(self, temp_dir="/tmp/autoplex_processing"):
        self.temp_dir = temp_dir
        os.makedirs(self.temp_dir, exist_ok=True)
        self._check_dependencies()

    def _check_dependencies(self):
        """Verify ffmpeg and demucs are available."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                check=True
            )
            version_line = result.stdout.decode().split('\n')[0]
            logger.info(f"FFmpeg found: {version_line}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("FFmpeg not found - remix features will fail")

        try:
            subprocess.run(
                ["demucs", "--help"],
                capture_output=True,
                check=True
            )
            logger.info("Demucs found")
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("Demucs not found - remix features will fail")

    def process_track(self, file_path: str, stem: str, gain_db: float) -> str:
        """
        Separates the track using Demucs, applies gain to target stem, and remixes.

        Args:
            file_path: Path to the input audio file
            stem: Target stem (bass, drums, vocals, other)
            gain_db: Gain to apply in dB (positive = boost, negative = reduce)

        Returns:
            Path to the output MP3 file
        """
        if stem not in VALID_STEMS:
            raise ValueError(f"Invalid stem '{stem}'. Must be one of: {VALID_STEMS}")

        if abs(gain_db) > MAX_GAIN_DB:
            raise ValueError(f"Gain must be between -{MAX_GAIN_DB} and +{MAX_GAIN_DB} dB")

        filename = os.path.basename(file_path)
        name_no_ext = os.path.splitext(filename)[0]

        # Demucs outputs to: {temp_dir}/separated/htdemucs_ft/{name_no_ext}/
        output_folder = os.path.join(
            self.temp_dir, "separated", "htdemucs_ft", name_no_ext
        )

        logger.info(f"Starting Demucs separation for: {filename}")
        logger.info(f"Target: {stem} {'boost' if gain_db > 0 else 'reduce'} {abs(gain_db)}dB")

        # 1. Run Demucs with fine-tuned model and shifts for quality
        self._run_demucs(file_path)

        # 2. Verify stems exist
        stem_paths = self._get_stem_paths(output_folder)

        # 3. Mix with FFmpeg (apply gain to target stem, use limiter)
        action = "Boost" if gain_db > 0 else "Reduce"
        output_filename = f"{name_no_ext} ({stem.capitalize()} {action}).mp3"
        output_path = os.path.join(self.temp_dir, output_filename)

        self._mix_with_ffmpeg(stem_paths, stem, gain_db, output_path)

        return output_path

    def _run_demucs(self, file_path: str):
        """Run Demucs AI separation with fine-tuned model."""
        command = [
            "demucs",
            "-n", "htdemucs_ft",      # Fine-tuned model for better quality
            "--shifts", "2",           # Random shifts for improved SDR
            "--out", os.path.join(self.temp_dir, "separated"),
            file_path
        ]

        try:
            subprocess.run(command, check=True, capture_output=True)
            logger.info("Demucs separation complete")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "Unknown error"
            logger.error(f"Demucs failed: {stderr}")
            raise RuntimeError(f"Audio separation failed: {stderr}")

    def _get_stem_paths(self, output_folder: str) -> dict:
        """Get paths to all stem files and verify they exist."""
        stem_paths = {}
        for stem in VALID_STEMS:
            path = os.path.join(output_folder, f"{stem}.wav")
            if not os.path.exists(path):
                raise RuntimeError(f"Stem file not found: {path}")
            stem_paths[stem] = path
        return stem_paths

    def _mix_with_ffmpeg(
        self,
        stem_paths: dict,
        target_stem: str,
        gain_db: float,
        output_path: str
    ):
        """
        Mix stems using FFmpeg with intelligent limiting.

        Uses amix with normalize=0 (manual gain staging) followed by
        alimiter to prevent clipping while maximizing loudness.
        """
        # Build filter complex
        # Input mapping: bass=0, drums=1, vocals=2, other=3
        stem_order = ["bass", "drums", "vocals", "other"]

        inputs = []
        filter_parts = []

        for i, stem in enumerate(stem_order):
            inputs.extend(["-i", stem_paths[stem]])

            if stem == target_stem:
                # Apply gain to target stem
                filter_parts.append(f"[{i}:a]volume={gain_db}dB[s{i}]")
            else:
                # Pass through unchanged
                filter_parts.append(f"[{i}:a]acopy[s{i}]")

        # Mix all stems with normalize=0 (we handle gain with limiter)
        mix_inputs = "".join(f"[s{i}]" for i in range(4))
        filter_parts.append(
            f"{mix_inputs}amix=inputs=4:duration=longest:dropout_transition=0:normalize=0[mixed]"
        )

        # Apply limiter to prevent clipping
        # limit=0.95 (-0.45dB) leaves headroom
        # attack=5ms, release=50ms for transparent limiting
        # asc=1 enables auto-leveling for loudness
        filter_parts.append(
            "[mixed]alimiter=limit=0.95:attack=5:release=50:asc=1:level=1[out]"
        )

        filter_complex = ";".join(filter_parts)

        # Try progressive bitrates to fit Discord's 8MB limit
        max_size_bytes = 8 * 1024 * 1024  # 8MB

        for bitrate in ["320k", "192k", "128k"]:
            logger.info(f"Encoding at {bitrate}...")

            command = [
                "ffmpeg", "-y",  # Overwrite output
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-codec:a", "libmp3lame",
                "-b:a", bitrate,
                output_path
            ]

            try:
                subprocess.run(command, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode() if e.stderr else "Unknown error"
                logger.error(f"FFmpeg failed: {stderr}")
                raise RuntimeError(f"Audio mixing failed: {stderr}")

            file_size = os.path.getsize(output_path)
            file_size_mb = file_size / (1024 * 1024)
            logger.info(f"Output size: {file_size_mb:.1f}MB at {bitrate}")

            if file_size <= max_size_bytes:
                logger.info(f"Final output: {output_path} ({file_size_mb:.1f}MB)")
                return

        # If we get here, even 128k was too large
        logger.warning(f"File still >8MB at 128k ({file_size_mb:.1f}MB)")

    def cleanup(self):
        """Removes the temporary directory contents."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
            os.makedirs(self.temp_dir, exist_ok=True)
            logger.info("Cleaned up temporary files")
