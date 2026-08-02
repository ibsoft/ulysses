from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import RLock


class LocalVoiceSummarizer:
    def __init__(self, model_path: Path, max_input_tokens: int, max_chunks: int, max_output_tokens: int) -> None:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self._lock = RLock()
        self.max_input_tokens = max_input_tokens
        self.max_chunks = max_chunks
        self.max_output_tokens = max_output_tokens
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(str(model_path), local_files_only=True)
        self.model.eval()
        try:
            import torch

            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model.to(self.device)
        except Exception:
            self.device = "cpu"

    def summarize(self, text: str) -> str:
        with self._lock:
            token_ids = self.tokenizer.encode(text, add_special_tokens=False)
            chunks = self._representative_chunks(token_ids)
            partials = [self._generate(chunk) for chunk in chunks]
            partials = [part for part in partials if part]
            if not partials:
                return ""
            combined = " ".join(partials)
            combined_ids = self.tokenizer.encode(combined, add_special_tokens=False)
            if len(partials) > 1 and len(combined_ids) <= self.max_input_tokens:
                final = self._generate(combined_ids)
                if final:
                    return final
            return combined

    def _representative_chunks(self, token_ids: list[int]) -> list[list[int]]:
        if len(token_ids) <= self.max_input_tokens:
            return [token_ids]
        available = max(1, min(self.max_chunks, (len(token_ids) + self.max_input_tokens - 1) // self.max_input_tokens))
        if available == 1:
            return [token_ids[: self.max_input_tokens]]
        maximum_start = len(token_ids) - self.max_input_tokens
        starts = [round(index * maximum_start / (available - 1)) for index in range(available)]
        return [token_ids[start : start + self.max_input_tokens] for start in starts]

    def _generate(self, token_ids: list[int]) -> str:
        import torch

        source = self.tokenizer.decode(token_ids, skip_special_tokens=True)
        encoded = self.tokenizer(
            "summarize: " + source,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_input_tokens,
        )
        encoded = {name: value.to(self.device) for name, value in encoded.items()}
        with torch.inference_mode():
            output = self.model.generate(
                **encoded,
                max_new_tokens=self.max_output_tokens,
                min_new_tokens=min(24, self.max_output_tokens),
                num_beams=2,
                no_repeat_ngram_size=3,
            )
        return self.tokenizer.decode(output[0], skip_special_tokens=True).strip()


@lru_cache(maxsize=2)
def get_local_voice_summarizer(
    model_path: str,
    max_input_tokens: int,
    max_chunks: int,
    max_output_tokens: int,
) -> LocalVoiceSummarizer:
    return LocalVoiceSummarizer(Path(model_path), max_input_tokens, max_chunks, max_output_tokens)
