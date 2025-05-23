import subprocess
import os
import tempfile
import json
import yaml
import logging
import http.server
import socketserver
import threading
import shutil
from typing import Optional, Dict, Any

from mcp.server.fastmcp import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Global variable to hold the server thread
server_thread = None
server_port = 8080
output_base_dir = "./mkslides_output"
latest_output_dir = os.path.join(output_base_dir, "latest")

# Custom HTTP request handler to serve from a specific directory
class SlidesHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Set the directory to serve from
        super().__init__(*args, directory=output_base_dir, **kwargs)

    def log_message(self, format, *args):
        # Suppress HTTP server logs unless they are errors
        if args[1] != '200':
            logger.info(f"[HTTP Server] {format % args}")

def start_server_in_thread():
    global server_thread
    if server_thread is not None and server_thread.is_alive():
        logger.info("[Setup] HTTP server is already running.")
        return

    # Ensure the base output directory exists before starting the server
    os.makedirs(output_base_dir, exist_ok=True)
    logger.info(f"[Setup] Ensuring base output directory exists for HTTP server: {output_base_dir}")

    # Use ThreadingTCPServer to allow multiple requests
    # Allow address reuse to prevent "Address already in use" errors on restart
    socketserver.TCPServer.allow_reuse_address = True
    handler = SlidesHTTPHandler
    try:
        httpd = socketserver.TCPServer(("", server_port), handler)
        logger.info(f"[Setup] Starting HTTP server on port {server_port}...")
        server_thread = threading.Thread(target=httpd.serve_forever)
        server_thread.daemon = True  # Allow the main program to exit even if the thread is running
        server_thread.start()
        logger.info("[Setup] HTTP server started in background thread.")
    except Exception as e:
        logger.error(f"[Error] Failed to start HTTP server: {e}")
        # Optionally, re-raise or handle the error appropriately

# Create an MCP server instance
mcp = FastMCP("MkSlides Server", dependencies=["mkslides"])


@mcp.tool()
def generate_slides(
    markdown_content: str,
    slides_theme: Optional[str] = None,
    slides_highlight_theme: Optional[str] = None,
    revealjs_options: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generates HTML presentation slides from Markdown content using the mkslides library.
    This tool converts raw Markdown text into a complete HTML presentation using Reveal.js
    and serves it via a local HTTP server.

    Args:
        markdown_content (str): Required. Raw Markdown text for the slides. Must include
            valid markdown syntax with slide separators (---).
        slides_theme (str, optional): Theme name for the slides. Overrides the default
            theme in mkslides. Available options: black, white, league, beige, night,
            serif, simple, solarized, moon, dracula, sky, blood.
        slides_highlight_theme (str, optional): Syntax highlighting theme for code blocks.
            Accepts any built-in theme name from highlight.js (e.g., 'github', 'monokai').
        revealjs_options (Dict[str, Any], optional): Dictionary of Reveal.js configuration options
            to merge with or override the default settings.

    Returns:
        str: A URL to view the generated HTML slides (e.g., http://localhost:8080/latest/index.html).
            The slides are served from an internal HTTP server accessible via the mapped host port.

    Raises:
        ValueError: If markdown_content is empty or not provided.
        RuntimeError: If the mkslides build command fails.
    Examples:
        Basic usage with minimal options:
        ```python
        slides_url = generate_slides(
            markdown_content="# My Presentation\n\n---\n\n## Slide 2\n\nContent"
        )
        # Open slides_url in a browser
        ```

        Advanced configuration with theme and Reveal.js options:
        ```python
        slides_url = generate_slides(
            markdown_content="# Advanced Presentation\n\n---\n\n## Content",
            slides_theme="dracula",
            slides_highlight_theme="monokai",
            revealjs_options={
                "transition": "slide",
                "controls": True,
                "progress": True
            }
        )
        # Open slides_url in a browser
        ```
"""
    if not markdown_content:
        logger.error("[Error] markdown_content was not provided.")
        raise ValueError("markdown_content must be provided.")

    # Create a temporary markdown file
    temp_md_file = tempfile.NamedTemporaryFile(mode='w+', suffix=".md", delete=False)
    temp_md_file.write(markdown_content)
    temp_md_file.close()
    input_path = temp_md_file.name
    logger.info(f"[Setup] Created temporary markdown file: {input_path}")

    temp_config_file = None
    config_arg = []

    # Handle configuration
    if slides_theme or slides_highlight_theme or revealjs_options:
        config = {}

        if slides_theme or slides_highlight_theme:
            config['slides'] = {}
            if slides_theme:
                config['slides']['theme'] = slides_theme
                logger.info(f"[Setup] Setting slides theme: {slides_theme}")
            if slides_highlight_theme:
                config['slides']['highlight_theme'] = slides_highlight_theme
                logger.info(f"[Setup] Setting slides highlight theme: {slides_highlight_theme}")

        if revealjs_options:
            config['revealjs'] = revealjs_options
            logger.info(f"[Setup] Setting Reveal.js options: {revealjs_options}")

        # Create a temporary config file
        temp_config_file = tempfile.NamedTemporaryFile(mode='w+', suffix=".yml", delete=False)
        yaml.dump(config, temp_config_file)
        temp_config_file.close()
        config_arg = ["-f", temp_config_file.name]
        logger.info(f"[Setup] Created temporary config file: {temp_config_file.name}")
        logger.info(f"[Setup] Config content: {json.dumps(config, indent=2)}")

    # Ensure the latest output directory exists and is empty
    if os.path.exists(latest_output_dir):
        shutil.rmtree(latest_output_dir)
        logger.info(f"[Setup] Cleared previous output directory: {latest_output_dir}")
    os.makedirs(latest_output_dir, exist_ok=True)
    logger.info(f"[Setup] Ensuring output directory exists: {latest_output_dir}")

    # Build the mkslides command
    # We want the output directly in latest_output_dir, so we build to a temp dir
    # and then move the contents.
    temp_build_dir = tempfile.mkdtemp()
    logger.info(f"[Setup] Created temporary build directory: {temp_build_dir}")

    command = ["mkslides", "build", input_path, "-d", temp_build_dir] + config_arg

    logger.info(f"[API] Executing mkslides build command: {' '.join(command)}")

    try:
        # Execute the command
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        logger.info(f"[API] mkslides build stdout:\n{result.stdout}")
        if result.stderr:
             logger.warning(f"[API] mkslides build stderr:\n{result.stderr}")

        logger.info(f"[API] mkslides build completed successfully.")

        # Log contents of the temporary build directory to verify structure
        logger.info(f"[Setup] Contents of temporary build directory ({temp_build_dir}): {os.listdir(temp_build_dir)}")

        # Move the generated files directly from the temp build dir to the latest output dir
        # Based on user feedback, mkslides places output directly in the target directory.
        try:
            for item in os.listdir(temp_build_dir):
                source_item_path = os.path.join(temp_build_dir, item)
                destination_item_path = os.path.join(latest_output_dir, item)
                shutil.move(source_item_path, destination_item_path)
                logger.info(f"[Setup] Moved '{item}' from {temp_build_dir} to {latest_output_dir}")
            logger.info(f"[Setup] Successfully moved all generated files to {latest_output_dir}")

        except Exception as e:
            logger.error(f"[Error] Failed to move generated files from {temp_build_dir} to {latest_output_dir}: {e}")
            # Re-raise the exception to indicate failure
            raise RuntimeError(f"Failed to move generated files: {e}")


    except subprocess.CalledProcessError as e:
        logger.error(f"[Error] mkslides build failed with exit code {e.returncode}")
        logger.error(f"[Error] stdout:\n{e.stdout}")
        logger.error(f"[Error] stderr:\n{e.stderr}")
        raise RuntimeError(f"mkslides build failed: {e.stderr}")
    except FileNotFoundError:
         logger.error("[Error] 'mkslides' command not found. Is it installed and in the PATH?")
         raise RuntimeError("'mkslides' command not found. Is it installed and in the PATH?")
    except Exception as e:
        logger.error(f"[Error] An unexpected error occurred during mkslides build: {e}")
        raise RuntimeError(f"An unexpected error occurred during mkslides build: {e}")
    finally:
        # Clean up temporary files and directories
        if temp_md_file and os.path.exists(temp_md_file.name):
            os.remove(temp_md_file.name)
            logger.info(f"[Setup] Cleaned up temporary markdown file: {temp_md_file.name}")
        if temp_config_file and os.path.exists(temp_config_file.name):
            os.remove(temp_config_file.name)
            logger.info(f"[Setup] Cleaned up temporary config file: {temp_config_file.name}")
        if os.path.exists(temp_build_dir):
            shutil.rmtree(temp_build_dir)
            logger.info(f"[Setup] Cleaned up temporary build directory: {temp_build_dir}")


    # Return the URL to the generated slides
    # Assuming the server is accessible on localhost at server_port
    return f"http://localhost:{server_port}/latest/index.html"

if __name__ == "__main__":
    start_server_in_thread()
    mcp.run()
