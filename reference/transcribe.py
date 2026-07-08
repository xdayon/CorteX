import argparse
import time
from faster_whisper import WhisperModel

def main():
    parser = argparse.ArgumentParser(description="Transcreve áudio de vídeos usando faster-whisper e a GPU (se disponível).")
    parser.add_argument("input_file", help="Caminho para o arquivo de vídeo ou áudio.")
    parser.add_argument("--model", default="medium", help="Tamanho do modelo (tiny, base, small, medium, large-v3). Padrão: medium.")
    parser.add_argument("--language", default=None, help="Idioma do áudio (ex: pt, en, es). Padrão: auto-detectar.")
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"], help="Dispositivo para rodar (cuda ou cpu). Padrão: cuda.")
    
    args = parser.parse_args()

    print(f"[{time.strftime('%H:%M:%S')}] Carregando o modelo '{args.model}' no dispositivo '{args.device}'...")
    if args.device == "cuda":
        try:
            # Tenta carregar na GPU (GTX 1060 não suporta float16 puro de forma eficiente, usamos int8)
            model = WhisperModel(args.model, device="cuda", compute_type="int8")
        except Exception as e:
            print(f"Erro ao carregar na GPU: {e}")
            print("Caindo para execução na CPU...")
            model = WhisperModel(args.model, device="cpu", compute_type="int8")
    else:
        model = WhisperModel(args.model, device="cpu", compute_type="int8")

    print(f"[{time.strftime('%H:%M:%S')}] Iniciando transcrição de: {args.input_file}")
    
    start_time = time.time()
    
    segments, info = model.transcribe(
        args.input_file,
        language=args.language,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt="Transcrição em português do Brasil de uma conversa de podcast. Ignore música, silêncio e vinhetas.",
    )

    print(f"Idioma detectado: {info.language} com probabilidade {info.language_probability:.2f}")

    # Cria arquivo de saída (.txt) e também imprime no terminal
    output_filename = args.input_file.rsplit('.', 1)[0] + "_transcricao.txt"
    
    with open(output_filename, "w", encoding="utf-8") as f:
        for segment in segments:
            linha = f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}"
            print(linha)
            f.write(linha + "\n")
            
    end_time = time.time()
    
    print(f"\n[{time.strftime('%H:%M:%S')}] Transcrição finalizada em {end_time - start_time:.2f} segundos!")
    print(f"O resultado foi salvo em: {output_filename}")

if __name__ == '__main__':
    main()
