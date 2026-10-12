import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  display as originalDisplay,
  human as originalHuman,
  statusLabel as originalStatus,
} from './model';
import type { Json } from './model';
import { korean } from './translations';

export type Language = 'en' | 'ko';
export const PREFERENCE_KEY = 'work-program-language';
type MessageKey = keyof typeof korean;
function messageKey(value: string): value is MessageKey {
  return Object.prototype.hasOwnProperty.call(korean, value);
}
export function language(value: unknown): Language | null {
  return value === 'ko' || value === 'en' ? value : null;
}
export function preferredLanguage(saved: unknown, program: unknown, browser: string): Language {
  return (
    language(saved) || language(program) || (browser.toLowerCase().startsWith('ko') ? 'ko' : 'en')
  );
}
function readPreference(): Language | null {
  try {
    return language(localStorage.getItem(PREFERENCE_KEY));
  } catch {
    return null;
  }
}
export function translationTools(locale: Language) {
  const localeName = locale === 'ko' ? 'ko-KR' : 'en-GB';
  const number = (value: number) => value.toLocaleString(localeName);
  const t = (key: string, values: Readonly<Record<string, number | string>> = {}) => {
    const template: string = locale === 'ko' && messageKey(key) ? korean[key] : key;
    return template.replace(/\{([^{}]+)\}/g, (token: string, name: string) => {
      if (!Object.prototype.hasOwnProperty.call(values, name)) return token;
      const value = values[name];
      if (value === undefined) return token;
      return typeof value === 'number' ? number(value) : value;
    });
  };
  const display = (value: Json | undefined, fallback = 'Unknown') => {
    if (value === null || value === undefined || value === '') return t(fallback);
    return typeof value === 'number' ? number(value) : originalDisplay(value);
  };
  const date = (value: Json | undefined) => {
    if (typeof value !== 'string' || !value) return t('Not recorded');
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf())
      ? t('Not recorded')
      : parsed.toLocaleString(localeName, { dateStyle: 'medium', timeStyle: 'short' });
  };
  const warning = (value: Json) => {
    const input = originalDisplay(value);
    for (const prefix of [
      'Pending submitted source changed:',
      'Pending submitted source could not be verified:',
      'Frozen evidence unavailable:',
      'Frozen evidence changed:',
      'Conflicting frozen evidence hashes:',
    ]) {
      if (input.startsWith(prefix))
        return t(`${prefix} {value}`, { value: input.slice(prefix.length).trim() });
    }
    if (input.startsWith('Derived view drift: ') && input.endsWith('; director may run render.'))
      return t('Derived view drift: {value}; director may run render.', {
        value: input.slice('Derived view drift: '.length, -'; director may run render.'.length),
      });
    return t(input);
  };
  return {
    locale,
    localeName,
    t,
    number,
    display,
    date,
    warning,
    human: (value: Json | undefined) => t(originalHuman(value)),
    statusLabel: (value: Json | undefined) => t(originalStatus(value)),
  };
}
type LanguageContextValue = ReturnType<typeof translationTools> & {
  readonly setPreference: (value: Language) => void;
  readonly setProgramLanguage: (value: Language | null) => void;
};
const fallback: LanguageContextValue = {
  ...translationTools('en'),
  setPreference: () => {},
  setProgramLanguage: () => {},
};
const LanguageContext = createContext<LanguageContextValue>(fallback);
export function LanguageProvider({ children }: { readonly children: ReactNode }) {
  const [preference, setPreferenceState] = useState(readPreference);
  const [programLanguage, setProgramLanguage] = useState<Language | null>(null);
  const locale = preferredLanguage(preference, programLanguage, navigator.language);
  const tools = useMemo(() => translationTools(locale), [locale]);
  const setPreference = useCallback((value: Language) => {
    setPreferenceState(value);
    try {
      localStorage.setItem(PREFERENCE_KEY, value);
    } catch {
      /* Keep this tab usable when storage is disabled. */
    }
  }, []);
  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = tools.t('Work program');
  }, [locale, tools]);
  const value = useMemo(
    () => ({ ...tools, setPreference, setProgramLanguage }),
    [tools, setPreference],
  );
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}
export function useI18n() {
  return useContext(LanguageContext);
}
