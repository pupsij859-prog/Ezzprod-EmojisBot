// Тексты онбординга: приветствие (один раз при первом входе) и инструкция.
// Картинки инструкции — живые сцены из элементов приложения (js/views/onboarding.js, SCENES).

export const WELCOME = {
  title: ['Стикеры и эмодзи', 'с твоим ником'],
  text: 'Выбери шаблоны, впиши ник или загрузи логотип, и пак появится в твоём Telegram.',
  gift: 'Первая генерация бесплатно',
  cta: 'Начать',
};

// text — строка или функция от профиля (state.me)
export const SLIDES = [
  { scene: 'pick', title: 'Выбери шаблоны', text: 'Отмечай карточки на главной. Каждый шаблон стоит одну генерацию.' },
  { scene: 'edit', title: 'Добавь ник и логотип', text: 'Нажми «Оформить», впиши ник, загрузи логотип, выбери заливку и обводку. Превью сразу покажет результат.' },
  { scene: 'build', title: 'Создай пак', text: 'Нажми «Создать пак». Бот соберёт его в Telegram и пришлёт ссылку в чат.' },
  { scene: 'tg', title: 'Добавь к себе', text: 'Открой ссылку и нажми «Добавить». Эмодзи появятся в клавиатуре, стикеры в панели стикеров.' },
  { scene: 'balance', title: 'Генерации', text: (me) => `Они не сгорают. Покупай пакетами или получай за друзей: +${me.referral_reward}, когда друг соберёт пак и купит генерации.` },
];

// Баннеры карточки профиля (assets/banners, 800×400). В карточке слева затемнение под аватаром и именем,
// поэтому главное в кадре сдвигаем вправо: size — масштаб фона, pos — background-position.
// ?v= — меняй при замене файлов: картинки кэшируются браузером на час.
export const BANNERS = [
  { id: 'sunset', src: 'assets/banners/sunset.webp?v=2', size: 'cover', pos: '50% 74%' },
  { id: 'kabachki', src: 'assets/banners/kabachki.webp?v=2', size: '150% auto', pos: '30% 2%' },
  { id: 'cat', src: 'assets/banners/cat.webp?v=2', size: '150% auto', pos: '30% 50%' },
  { id: 'corgi', src: 'assets/banners/corgi.webp?v=2', size: '150% auto', pos: '40% 46%' },
  { id: 'pug-glasses', src: 'assets/banners/pug-glasses.webp?v=2', size: '150% auto', pos: '0% 42%' },
  { id: 'pug-cap', src: 'assets/banners/pug-cap.webp?v=2', size: 'cover', pos: '50% 55%' },
];
