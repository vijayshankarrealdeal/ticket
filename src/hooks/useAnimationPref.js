import { useEffect, useState } from 'react';

const KEY = 'workspacex-animations';
export const ANIMATION_LEVELS = ['full', 'subtle', 'off'];

function readInitial() {
  if (typeof window === 'undefined') return 'full';
  const saved = localStorage.getItem(KEY);
  if (ANIMATION_LEVELS.includes(saved)) return saved;
  const prefersReduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  return prefersReduced ? 'subtle' : 'full';
}

export function useAnimationPref() {
  const [level, setLevel] = useState(readInitial);

  useEffect(() => {
    document.documentElement.setAttribute('data-animations', level);
    localStorage.setItem(KEY, level);
  }, [level]);

  return { level, setLevel };
}
