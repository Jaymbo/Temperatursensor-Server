// Zentrale API-Basis-URL Ermittlung
// Priorität: VITE_API_URL (kann komplette URL inkl. Schema/Port sein)
// Fallback: aktueller Host + Port 8000
// Lokale Dev-Sonderfall: wenn Host localhost/127.* und kein Port 8000 bereits in VITE_API_URL, nutze http://localhost:8000
export const getApiBase = (): string => {
  const viteEnv = (import.meta as any).env?.VITE_API_URL as string | undefined;
  const nodeEnv = (globalThis as any)?.process?.env?.VITE_API_URL as string | undefined;
  const env = viteEnv ?? nodeEnv;
  if (env && env.trim() !== "") {
    return env.replace(/\/$/, "");
  }
  const host = window.location.hostname;
  // Wenn Frontend bereits über Port 8000 ausgeliefert wird, gleiche Origin verwenden
  if (window.location.port === "8000") {
    return `${window.location.protocol}//${host}:8000`;
  }
  return `${window.location.protocol}//${host}:8000`;
};

export const buildApiUrl = (path: string) => `${getApiBase()}${path.startsWith('/') ? path : '/' + path}`;

export const apiFetch: typeof fetch = (input: RequestInfo | URL, init?: RequestInit) => {
  if (typeof input === 'string' && input.startsWith('/')) {
    return fetch(buildApiUrl(input), init);
  }
  return fetch(input, init);
};
