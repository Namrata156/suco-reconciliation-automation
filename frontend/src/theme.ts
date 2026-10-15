import { createTheme } from '@mantine/core';

/**
 * Accessibility (design 7.4): darker filled shades and automatic text
 * contrast on coloured badges/buttons, a visible focus ring for keyboard
 * users, and the system font stack (nothing is fetched from the internet).
 */
export const theme = createTheme({
  primaryColor: 'blue',
  primaryShade: { light: 8, dark: 6 },
  autoContrast: true,
  luminanceThreshold: 0.35,
  focusRing: 'auto',
  defaultRadius: 'sm',
  components: {
    Table: { defaultProps: { striped: true, highlightOnHover: true, verticalSpacing: 6 } },
  },
});
