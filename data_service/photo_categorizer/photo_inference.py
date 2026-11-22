"""Text and image inference for the photo worker."""
import json
import os
import re

import anthropic
import requests


class InferenceError(RuntimeError):
    """A provider request failed and the worker should stop."""


class Inference:
    def __init__(self, *, local, model, ollama_url, ollama_timeout=600):
        self.local = local
        self.model = model
        self.provider = 'Ollama' if local else 'Anthropic'
        self.ollama_url = ollama_url.rstrip('/')
        self.ollama_timeout = ollama_timeout
        self.capabilities = None
        self.session = requests.Session() if local else None
        if self.session is not None:
            self.session.trust_env = False
        self.client = None if local else anthropic.Anthropic(
            api_key=os.environ['ANTHROPIC_API_KEY'], max_retries=0,
        )

    def json(self, prompt, *, tokens, image=None, schema=None):
        if self.local:
            return self._ollama(prompt, tokens=tokens, image=image, schema=schema)
        content = prompt if image is None else [
            {'type': 'image', 'source': image}, {'type': 'text', 'text': prompt},
        ]
        try:
            response = self.client.messages.create(
                model=self.model, max_tokens=tokens, messages=[{'role': 'user', 'content': content}],
            )
        except anthropic.AuthenticationError as exc:
            raise InferenceError('Anthropic authentication failed') from exc
        except anthropic.PermissionDeniedError as exc:
            raise InferenceError('Anthropic access was denied') from exc
        if response.stop_reason == 'max_tokens':
            raise ValueError('Truncated model response')
        text = ''.join(getattr(block, 'text', '') for block in response.content)
        return parse_response(text, 'Anthropic')

    def _ollama(self, prompt, *, tokens, image, schema):
        # The local Gemma MLX runner does not accept Ollama's format option.
        if schema:
            prompt += f' Return an object matching this JSON schema: {json.dumps(schema)}'
        message = {'role': 'user', 'content': prompt}
        if image is not None:
            message['images'] = [image['data']]
        request = {
            'model': self.model, 'messages': [message], 'stream': False, 'keep_alive': '10m',
            'options': {'temperature': 0, 'num_predict': tokens},
        }
        if self.capabilities is None or 'thinking' in self.capabilities:
            request['think'] = False
        payload = self._local_request('/api/chat', request, timeout=self.ollama_timeout)
        if payload.get('done_reason') == 'length':
            raise ValueError('Truncated model response')
        try:
            text = payload['message']['content']
        except (KeyError, TypeError) as exc:
            raise ValueError('Expected a message from Ollama') from exc
        return parse_response(text, 'Ollama')

    def verify_vision(self):
        """Check the installed model before submitting any listing photos."""
        metadata = self._local_request('/api/show', {'model': self.model}, timeout=10)
        self.capabilities = set(metadata.get('capabilities') or ())
        if 'vision' not in self.capabilities:
            raise InferenceError(
                f'Ollama model {self.model} does not report vision support. '
                'Choose an installed vision model with --local --model.'
            )

    def _local_request(self, path, payload, *, timeout):
        try:
            response = self.session.post(
                f'{self.ollama_url}{path}', timeout=(5, timeout), allow_redirects=False,
                json=payload,
            )
            response.raise_for_status()
            if response.is_redirect:
                raise InferenceError('Ollama returned a redirect')
            data = response.json()
        except requests.Timeout as exc:
            advice = ' Use --ollama-timeout when the local model is busy.' if path == '/api/chat' else ''
            raise InferenceError(f'Ollama did not reply within {timeout} seconds.{advice}') from exc
        except requests.RequestException as exc:
            raise InferenceError(
                f'Ollama request to {self.ollama_url}{path} failed. '
                f'Check that Ollama is running and {self.model} is installed.'
            ) from exc
        if not isinstance(data, dict):
            raise ValueError('Expected an Ollama response object')
        return data


def parse_response(text, provider):
    if not isinstance(text, str):
        raise ValueError(f'Expected a JSON response from {provider}')
    text = text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    try:
        result = json.loads(fenced.group(1) if fenced else text)
    except json.JSONDecodeError:
        if provider != 'Anthropic':
            raise ValueError(f'Expected a JSON response from {provider}') from None
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            raise ValueError(f'Expected a JSON response from {provider}') from None
        result = json.loads(match.group(0))
    if not isinstance(result, dict):
        raise ValueError(f'Expected a JSON object from {provider}')
    return result
