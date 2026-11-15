"use strict";
const VIEW_KEY = 'oto-startup-view';
const validView = (value) => value === '2d' || value === '3d';
window.otoDesktop = {
    startupView: '2d',
    getStartupView() {
        const value = localStorage.getItem(VIEW_KEY);
        this.startupView = validView(value) ? value : '2d';
        return this.startupView;
    },
    setStartupView(view) {
        this.startupView = view;
        localStorage.setItem(VIEW_KEY, view);
    },
};
