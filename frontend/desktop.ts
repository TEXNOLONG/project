type AirportView = '2d' | '3d';

interface OtoDesktopPreferences {
  startupView: AirportView;
  getStartupView(): AirportView;
  setStartupView(view: AirportView): void;
}

interface Window {
  otoDesktop: OtoDesktopPreferences;
}

const VIEW_KEY = 'oto-startup-view';
const validView = (value: string | null): value is AirportView => value === '2d' || value === '3d';

window.otoDesktop = {
  startupView: '2d',
  getStartupView(): AirportView {
    const value = localStorage.getItem(VIEW_KEY);
    this.startupView = validView(value) ? value : '2d';
    return this.startupView;
  },
  setStartupView(view: AirportView): void {
    this.startupView = view;
    localStorage.setItem(VIEW_KEY, view);
  },
};

