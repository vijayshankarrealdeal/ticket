import { useEffect, useRef, useState } from 'react';

export function useDelayUnmount(isOpen, durationMs = 200) {
  const [mounted, setMounted] = useState(isOpen);
  const [entering, setEntering] = useState(false);
  const timerRef = useRef(null);
  const rafRef = useRef(null);

  useEffect(() => {
    clearTimeout(timerRef.current);
    cancelAnimationFrame(rafRef.current);

    if (isOpen) {
      setMounted(true);
      setEntering(true);
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = requestAnimationFrame(() => setEntering(false));
      });
    } else if (mounted) {
      setEntering(false);
      timerRef.current = setTimeout(() => setMounted(false), durationMs);
    }

    return () => {
      clearTimeout(timerRef.current);
      cancelAnimationFrame(rafRef.current);
    };
  }, [isOpen, durationMs]);

  const state = !mounted ? 'closed' : !isOpen ? 'exiting' : entering ? 'entering' : 'open';

  return { mounted, state, isOpen };
}
