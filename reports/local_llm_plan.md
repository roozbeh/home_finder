# Local LLM Integration Plan (gemma4:12b)

## 1. Feasibility Verification
The change is **Possible**. The `classify` function is designed to accept a `client` object, which allows for the injection of an Ollama-compatible client without refactoring the core categorization logic.

## 2. Proposed Implementation
To integrate `gemma4:12b` as a local classifier, the following changes will be made to `data_service/photo_categorizer/categorize_photos.py`:

### Code Changes
- **Flag Addition**: Add a `--local` flag to the `argparse` section.
- **Implementation**:
    - Use `requests` to interact with the local Ollama server (`http://localhost:11434/api/chat/completions`).
    - This avoids adding heavy new dependencies like `openai` to the environment.
- **Logic Update**:
    - Modify `main()` to check for the `--local` flag.
    - If present, instantiate a `LocalOllamaClient` instead of `anthropic.Anthimeric`.
    - Set the `MODEL` to `gemma4:12b` automatically when in local mode.

### Interaction Model
- **Prompting**: The prompt will be simplified to ensure better adherence from `gemma4`.
- **Processing**: The `classify` function will remain the same but will receive a client that returns a standard response format.

## 3. Validation Plan
To ensure the local model is producing accurate classifications:
1. **Unit Test**: Create a test script that passes 5 representative images (Kitchen, Bedroom, Living Room, Exterior, Bathroom).
2. **Assertion**: Verify that `gemma4:12b` assigns the correct category with > 80% confidence.
3. **Regression**: Ensure existing Claude-based logic remains unaffected when the `--local` flag is off.

## 4. Impact Analysis
- **Hardware**: Requires local GPU memory for `gemma4:12b`.
- **Cost**: Reduces per-request API costs to zero.
- **Speed**: Dependent on local hardware (expected to be slower than Claude Haiku).
