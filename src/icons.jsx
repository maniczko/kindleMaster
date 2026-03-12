import React from "react";

export const Icon = ({ d, size = 16, className = "", strokeWidth = 1.75 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    <path d={d} />
  </svg>
);

export const IcoBrain = ({ size = 16 }) => (
  <Icon
    size={size}
    d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18a4 4 0 1 0 7.967-1.517 4 4 0 0 0 .556-6.588 4 4 0 0 0-2.526-5.77A3 3 0 0 0 12 5"
  />
);

export const IcoTrophy = ({ size = 16 }) => (
  <Icon size={size} d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6m12 5h1.5a2.5 2.5 0 0 0 0-5H18M12 12v4m-4 4h8M3 3h18v6a9 9 0 0 1-18 0V3z" />
);

export const IcoClock = ({ size = 16 }) => <Icon size={size} d="M12 2a10 10 0 1 1 0 20A10 10 0 0 1 12 2zm0 4v6l4 2" />;
export const IcoCalendar = ({ size = 16 }) => <Icon size={size} d="M8 2v4M16 2v4M3 8h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />;
export const IcoBook = ({ size = 16 }) => <Icon size={size} d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z" />;
export const IcoSettings = ({ size = 16 }) => <Icon size={size} d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />;
export const IcoRefresh = ({ size = 16 }) => <Icon size={size} d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />;
export const IcoChat = ({ size = 16 }) => <Icon size={size} d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />;
export const IcoLeft = ({ size = 16 }) => <Icon size={size} d="M19 12H5M12 19l-7-7 7-7" />;
export const IcoRight = ({ size = 16 }) => <Icon size={size} d="M5 12h14M12 5l7 7-7 7" />;
export const IcoUpload = ({ size = 16 }) => <Icon size={size} d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />;
export const IcoCloud = ({ size = 16 }) => <Icon size={size} d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />;
export const IcoCheck = ({ size = 16 }) => <Icon size={size} d="M20 6L9 17l-5-5" />;
export const IcoCross = ({ size = 16 }) => <Icon size={size} d="M18 6L6 18M6 6l12 12" />;
export const IcoTrending = ({ size = 16 }) => <Icon size={size} d="M3 17l6-6 4 4 7-8M14 7h6v6" />;
export const IcoBolt = ({ size = 16 }) => <Icon size={size} d="M13 2L4 14h6l-1 8 9-12h-6l1-8z" />;
export const IcoTarget = ({ size = 16 }) => <Icon size={size} d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8z" />;