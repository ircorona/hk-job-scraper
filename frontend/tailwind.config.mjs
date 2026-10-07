/** Paper and ink design system, shared with the Mexico dashboard
 *  (mexico-job-scraper/knowledge/2026-10-06_design_system.md).
 *  One orange accent, used only where it carries meaning.
 *  @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  // Classes that only appear inside JS strings built at runtime.
  safelist: [
    'hidden', 'bg-well', 'text-ink-2', 'text-ink-3', 'bg-accent-tint', 'text-accent-deep',
    'text-accent-ink', 'whitespace-nowrap', 'tabular-nums', 'font-medium', 'font-semibold',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        serif: ['Newsreader', 'Georgia', 'serif'],
      },
      colors: {
        paper: '#FAF8F5',
        surface: '#FFFFFF',
        well: '#F3EFE9',
        rule: { DEFAULT: '#E7E2DA', strong: '#D6CFC4' },
        ink: { DEFAULT: '#1C1B19', 2: '#57534E', 3: '#716A64' },
        accent: { DEFAULT: '#FF6B00', ink: '#C2410C', deep: '#9A3412', tint: '#FFF1E6' },
      },
    },
  },
  plugins: [],
};
