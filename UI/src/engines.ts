// Engines, their connections in fallback order, and pipeline settings: what AI and engines configures.
// Kept in the workspace (App) so every page reads the same values: Review flags below `accept`, profiles show where each step runs.
import {
  ArrowRoutingRegular, DocumentTextRegular, ImageSparkleRegular, LayoutCellFourRegular, ScanTextRegular, SparkleRegular, LinkRegular, ShieldCheckmarkRegular,
} from '@fluentui/react-icons';
import type { FluentIcon } from '@fluentui/react-icons';
import type { Profile } from './types';

export interface Engine { id: string; name: string; tech: string; icon: FluentIcon; models: string[]; runtimes: string[]; providers: string[] }
export type AuthKind = 'apikey' | 'bearer' | 'oauth' | 'iam' | 'none';
/** One way to run an engine: a local server or a cloud API, tried in fallback order. */
export interface Conn {
  id: string; name: string; mode: 'local' | 'api'; status: 'connected' | 'offline'; latency: string;
  model?: string; runtime?: string; host?: string; workers?: number;
  provider?: string; endpoint?: string; auth?: AuthKind; key?: string; tenant?: string; client?: string; role?: string;
  modelId?: string; timeout?: number; retries?: number; rate?: number; budget?: number; spent?: number; headers?: string;
}

const LLM_PROVIDERS = ['Anthropic Claude', 'Azure OpenAI', 'OpenAI-Compatible', 'Google Vertex AI', 'AWS Bedrock', 'Custom REST'];
export const ENGINES: Engine[] = [
  { id: 'router', name: 'Document type detection', tech: 'Router, picks the profile', icon: ArrowRoutingRegular, models: ['DocType Classifier', 'Rules Only'], runtimes: ['ONNX Runtime'], providers: ['Azure Document Intelligence Classifier', 'Custom REST'] },
  { id: 'vector', name: 'Digital text', tech: 'Vector text extraction for digital PDFs', icon: DocumentTextRegular, models: ['PyMuPDF Text Layer', 'pdfplumber'], runtimes: ['Python'], providers: ['Custom REST'] },
  { id: 'upscaler', name: 'Image enhancement', tech: 'Resolution upscaler for scans and photos', icon: ImageSparkleRegular, models: ['SwinIR Document x2', 'SwinIR Document x4', 'Bicubic'], runtimes: ['PyTorch', 'ONNX Runtime'], providers: ['Custom REST'] },
  { id: 'layout', name: 'Page layout', tech: 'Document layout analyser', icon: LayoutCellFourRegular, models: ['PP-DocLayout-L', 'MinerU 2.5', 'Docling Heron'], runtimes: ['ONNX Runtime', 'PaddlePaddle'], providers: ['Azure Document Intelligence', 'Google Document AI', 'AWS Textract', 'Custom REST'] },
  { id: 'ocr', name: 'Text recognition', tech: 'OCR engine', icon: ScanTextRegular, models: ['PP-OCRv5 Server', 'PP-OCRv5 Mobile', 'Tesseract 5'], runtimes: ['PaddlePaddle', 'ONNX Runtime'], providers: ['Azure AI Vision Read', 'Google Cloud Vision', 'AWS Textract', 'Custom REST'] },
  { id: 'llm', name: 'AI model', tech: 'LLM, local or API', icon: SparkleRegular, models: ['Qwen3-VL-8B Invoice LoRA', 'Qwen3-VL-8B Instruct'], runtimes: ['llama.cpp', 'Ollama', 'vLLM', 'Transformers'], providers: LLM_PROVIDERS },
  { id: 'decision', name: 'Field matching', tech: 'Decision model and validation rules', icon: LinkRegular, models: ['LAYA Cross-Encoder + bge-small', 'Rules Only'], runtimes: ['ONNX Runtime', 'PyTorch'], providers: ['Azure ML Endpoint', 'AWS SageMaker Endpoint', 'Custom REST'] },
  { id: 'evaluator', name: 'Accuracy check', tech: 'LLM evaluator', icon: ShieldCheckmarkRegular, models: ['Qwen3-8B Judge'], runtimes: ['llama.cpp', 'Ollama', 'vLLM'], providers: LLM_PROVIDERS },
];
export const AUTH: Record<AuthKind, string> = { apikey: 'API key', bearer: 'Bearer token', oauth: 'OAuth client credentials', iam: 'Cloud IAM role', none: 'None' };

export const local = (e: Engine, port: number, extra: Partial<Conn> = {}): Conn => ({ id: `${e.id}-local`, name: `Local ${e.models[0].split(' ')[0]}`, mode: 'local', model: e.models[0], runtime: e.runtimes[0], host: `http://127.0.0.1:${port}`, workers: 2, status: 'connected', latency: extra.latency || '', ...extra });
export const api = (o: Partial<Conn> & { id: string; name: string }): Conn => ({ mode: 'api', auth: 'apikey', key: '', modelId: '', timeout: 60, retries: 2, rate: 120, budget: 0, spent: 0, headers: '', status: 'offline', latency: '', ...o });

export const E: Record<string, Engine> = Object.fromEntries(ENGINES.map((e) => [e.id, e]));
export const INITIAL_CONNS: Record<string, Conn[]> = {
  router: [local(E.router, 8094, { latency: '60 ms' })],
  vector: [local(E.vector, 8095, { latency: '90 ms' })],
  upscaler: [local(E.upscaler, 8096, { latency: '1.1 s' })],
  layout: [local(E.layout, 8091, { latency: '140 ms' })],
  ocr: [local(E.ocr, 8092, { latency: '310 ms' }), api({ id: 'ocr-az', name: 'Azure Vision Read', provider: 'Azure AI Vision Read', endpoint: 'https://harsha-vision.cognitiveservices.azure.com/', auth: 'oauth', budget: 200, spent: 38, status: 'connected', latency: '640 ms' })],
  llm: [local(E.llm, 8080, { latency: '2.4 s' }), api({ id: 'llm-claude', name: 'Claude API', provider: 'Anthropic Claude', endpoint: 'https://api.anthropic.com/v1/messages', modelId: 'claude-sonnet-5-5', budget: 500, spent: 410, status: 'connected', latency: '1.9 s' })],
  decision: [local(E.decision, 8093, { latency: '35 ms' })],
  evaluator: [api({ id: 'eval-claude', name: 'Claude API', provider: 'Anthropic Claude', endpoint: 'https://api.anthropic.com/v1/messages', modelId: 'claude-sonnet-5-5', budget: 500, spent: 410 }), local(E.evaluator, 8081, { latency: '3.8 s' })],
};

export type Settings = typeof SETTINGS;
export const SETTINGS = {
  minDocConf: 0.7, unknown: 'review', rules: [['TAX INVOICE + DACHSER', 'Freight Invoice'], ['PURCHASE ORDER', 'Purchase Order'], ['DELIVERY CHALLAN', 'Delivery Note']] as [string, string][],
  sources: [['Scanner: Stores Gate 2', 'Delivery Note'], ['Folder: Aug Freight', 'Freight Invoice']] as [string, string][],
  minCoverage: 85, mixedPages: true, tolerance: 2,
  upMode: 'auto', belowDpi: 150, maxScale: 'x4',
  threshold: 0.5, tables: true, order: true, maxPages: 50,
  dpi: 200, trust: 0.95, langs: ['English', 'Hindi', 'Gujarati'],
  temperature: 0, maxTokens: 2048, json: true, prompt: 'Return only JSON matching the profile schema. Cite every value to a text span. Leave unprinted values blank.',
  accept: 0.85, autoAccept: false, checks: ['Charges add up', 'Tax sum', 'Container number (ISO 6346)', 'MAWB check digit', 'Date range'],
  sample: 100, criteria: ['Value is on the document', 'Format is valid', 'Fields agree with each other'], onFail: 'review', pass: 0.85,
  redact: ['GSTIN', 'PAN', 'Bank account', 'Phone', 'Email'], redactMode: 'mask', logPayloads: false, retention: 30,
};

export interface EngineState { conns: Record<string, Conn[]>; cfg: Settings }
export const ENGINE_DEFAULTS: EngineState = { conns: INITIAL_CONNS, cfg: SETTINGS };

/** Where each step of reading runs for a profile: the connections tried in order, and whether data can leave the network. */
export function whereItRuns({ conns }: EngineState, p: Pick<Profile, 'onPremOnly' | 'evaluator'>) {
  return ENGINES.filter((e) => e.id !== 'evaluator' || p.evaluator).map((e) => {
    const chain = conns[e.id] || [];
    const used = p.onPremOnly ? chain.filter((c) => c.mode === 'local') : chain;
    const skipped = chain.filter((c) => !used.includes(c));
    return { engine: e, used, skipped, leaves: used.some((c) => c.mode === 'api') ? (used[0]?.mode === 'api' ? 'always' : 'fallback') : 'never' } as const;
  });
}
