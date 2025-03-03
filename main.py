# imports
import mysql.connector
import re
# import of telebot
from telebot import TeleBot , types
from telebot.storage import StateMemoryStorage
from telebot.handler_backends import State,StatesGroup
from telebot import custom_filters
# my import
import config

# some settings
state_storage = StateMemoryStorage()
texts = dict()
chat_ids = list()
# create a bot
bot = TeleBot(token=config.token,state_storage=state_storage)

# storage
class Support(StatesGroup):
    text = State()
    response = State()
class State_storage(StatesGroup):
    Ads_state = State()

# This function tries to see if the user is a member of our channels or not
def check_join(user):
    for channel in config.channels:
        is_member = bot.get_chat_member(chat_id=channel, user_id=user)
        if is_member.status in ['kicked', 'left']:
            return False
    return True
# if somone not join in we channels this func is run 
def join_channel(message):
    text = '''
    برای استفاده از ربات ما باید عضو چنل های ما باشید:)
'''
    markup = types.InlineKeyboardMarkup()
    for link in config.links:
        button = types.InlineKeyboardButton(text="عضویت", url=link)
        markup.add(button)
    bot.send_message(chat_id=message.chat.id, text =text,reply_markup=markup)
# for get user balance in database
def user_balance(user):
    sql = f"SELECT balance FROM users WHERE id = {user}"
    with mysql.connector.connect(**config.db) as connection:
        with connection.cursor() as mycursor:
            mycursor.execute(sql)
            result = mycursor.fetchone()
    return result

def change_balance(user):
    with mysql.connector.connect(**config.db) as connection:
            with connection.cursor() as mycursor :
                sql= f"UPDATE users SET balance = balance - 15000 WHERE id = {user}"
                mycursor.execute(sql)
                connection.commit()
                return user_balance(user)

def get_user_id(text):
    pattern = re.pattern = r"شناسه کاربر : \d+"
    user = int(re.findall(pattern=pattern, string=text)[0].split(':')[1])
    return user

# escape_special_characters
def escape_special_characters(text):
    special_characters = r"([\*\_\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])"
    return re.sub(special_characters, r'\\\1', text)

# start a bot and check user is a new member or not and if user is new member then create in database
@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup()
    key_1 = types.KeyboardButton(text='💠 ثبت آگهی')
    markup.add(key_1)
    markup.add("👤 حساب کاربری", "💵 شارژ حساب", "🎰 زیرمجموعه گیری", "☎️ پشتیبانی")

    member = check_join(message.from_user.id)
    try:
        with mysql.connector.connect(**config.db) as connection:
            with connection.cursor() as mycursor :
                # create a new user
                sql = f'INSERT INTO users(id) VALUES ({message.from_user.id})'
                mycursor.execute(sql) 
                connection.commit()
                # This section is for someone who is invited to use the bot using the referral link
                token = message.text.split()
                bot.send_message(chat_id=message.chat.id, text=f'خوش آمدید کاربر {message.from_user.username}\nاصلاعات شما ثبت شد',reply_markup=markup)
                if len(token) > 1:
                    sql_referral = f"UPDATE users SET balance = balance + 1000 WHERE id = {token[1]}"
                    mycursor.execute(sql_referral)
                    connection.commit()
                    bot.send_message(chat_id=token[1],text=f'کاربر گرامی\nفردی با نام کاربری {message.from_user.username}با لینک شما دعوت شد\n1000به موجودی شما اضافه شد .')
                # if user is not join us channel run a join_channel function
                if member == False:
                    join_channel(message)
    except:
        if member:
            bot.send_message(chat_id=message.chat.id, text=f'خوش آمدید کاربر {message.from_user.username}',reply_markup=markup)
        else:
            join_channel(message)
# This function tries to tell the user what the bot is doing
@bot.message_handler(commands=['help'])
def help(message):
    member = check_join(message.from_user.id)
    text = '''سلام این ربات برای ثبت اگهی است  شما با افزایش موجودی میتوانید اگهی خود را در کانال ما ثبت و ارسال کنید
    برای دیدن موجودی حساب خود دکمه حساب کاربری را بزنید.'''
    bot.reply_to(message,text = text)
    if member == False:
        join_channel(message)

@bot.message_handler(func= lambda message: message.text == '💠 ثبت آگهی')
def Ads(message):
    balance = user_balance(message.from_user.id)
    if balance[0] >= 15000:
        text = 'موجودی حساب شما کافی بود \n\nخب حالا آگهی خودتو بفرست!'
        bot.send_message(chat_id=message.chat.id,text=text)
        bot.set_state(user_id=message.from_user.id,state=State_storage.Ads_state,chat_id=message.chat.id)
    else:
        bot.send_message(chat_id=message.chat.id,text=f'موجودی حساب شما برابر {balance[0]} \nبرای ثبت آگهی باید بیشتر از 15000 موجودی داشته باشید \nلطفا حساب خود را با دکمه افزایش موجودی اقدام به افزایش بکنید')


@bot.message_handler(state=State_storage.Ads_state,content_types=['photo','text','video'])
def get_Ads(message):
    markup = types.InlineKeyboardMarkup()
    button_1 = types.InlineKeyboardButton(text="رد کردن", callback_data="deny")
    button_2 = types.InlineKeyboardButton(text="تایید کردن", callback_data="confirm")
    markup.add(button_1, button_2)

    forwarded_message = bot.forward_message(chat_id=config.admin_id, from_chat_id=message.chat.id, message_id=message.message_id)
    
    bot.send_message(
        chat_id=config.admin_id,
        text=f'کاربر {message.from_user.username} درخواست ثبت آگهی دارد در صورت تاید دکمه تایید رو بفرستید\n\nشناسه کاربر : {message.from_user.id}',
        reply_markup=markup,
        reply_to_message_id=forwarded_message.message_id
        )
    bot.send_message(chat_id=message.chat.id,text='درخواست شما ثبت شد\nلطفا صبر کنید به زودی نتیجه آن برای شما ارسال میشود')
    bot.delete_state(chat_id=message.chat.id,user_id=message.from_user.id)

@bot.callback_query_handler(func=lambda call:call.data=='deny')
def deny(call):
    user = get_user_id(call.message.text)
    markup = types.InlineKeyboardMarkup()
    button = types.InlineKeyboardButton(text="با موفقیت رد شد", callback_data="not allowed")
    markup.add(button)

    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    bot.send_message(chat_id=user, text="درخواست شما رد شد")

@bot.callback_query_handler(func=lambda call:call.data=='confirm')
def confirm(call):
    user = get_user_id(call.message.text)
    for channel in config.channels:
        bot.copy_message(
            chat_id=channel,
            from_chat_id=call.message.chat.id,
            message_id=call.message.reply_to_message.message_id
    )
    
    markup = types.InlineKeyboardMarkup()
    button = types.InlineKeyboardButton(text="با موفقیت تایید شد", callback_data="allowed")
    markup.add(button)

    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    balance = change_balance(user)
    bot.send_message(chat_id=int(user), text=f"مبلغ 15000 از حساب شما کم شد !!!!!\n\nموجودی حال حاضر شما برابر است با {balance[0]}تومان")
    bot.send_message(chat_id=int(user), text="آگهی شما ثبت شد")

    

@bot.message_handler(func=lambda message:message.text=='👤 حساب کاربری')
def account(message):
    member = check_join(message.from_user.id)
    if member:
        with mysql.connector.connect(**config.db) as connection:
            with connection.cursor() as mycursor:
                sql = f'SELECT balance from users WHERE id={message.from_user.id}'
                mycursor.execute(sql)
                balance = mycursor.fetchone()
                
        text=f"""📊 اطلاعات حساب کاربری شما:

    👤 نام کاربری : <a href='tg://user?id={message.from_user.id}'>{message.from_user.first_name}</a>
    🪪 شناسه کاربری : <code>{message.from_user.id}</code>
    💰 موجودی : {balance[0]} تومان"""
        bot.send_message(chat_id=message.chat.id, text=text, parse_mode="HTML")
    else:
        join_channel(message)

# @bot.message_handler(func=lambda message: message.text == '/test')
# def test(message):
#     member = check_join(message.from_user.id)
#     if member:
#         bot.send_message(chat_id=message.chat.id,text=message.chat.id)
#     else:
#         join_channel(message)

        
# this function for referral
@bot.message_handler(func= lambda message: message.text == "🎰 زیرمجموعه گیری")
def referral(message):
    memmber = check_join(message.from_user.id)
    if memmber:
        with open("Referrals header - sm.webp", "rb") as photo:
            bot.send_photo(chat_id=message.chat.id, photo=photo, caption=f"""این لینک رفرال شما هست:
                
https://t.me/Ads_for_channelbot?start={message.from_user.id}""")
    else:
        join_channel(message)



# support
@bot.message_handler(func=lambda message: message.text == '☎️ پشتیبانی')
def support_user(message):
    member = check_join(message.from_user.id)
    if member:
        bot.send_message(chat_id=message.chat.id, text="لطفا پیام خود را ارسال کنید:")
        bot.set_state(user_id=message.from_user.id, state=Support.text, chat_id=message.chat.id)
    else:
        join_channel(message)

@bot.message_handler(state=Support.text)
def sup_text(message):
    markup = types.InlineKeyboardMarkup()
    button = types.InlineKeyboardButton(text="پاسخ", callback_data=message.from_user.id)
    markup.add(button)

    bot.send_message(chat_id=1297080099, text=f"پیامی از طرف دریافت کرد <code>{message.from_user.id}</code> با نام کاربری @{message.from_user.username}:\nمتن پیام:\n\n<b>{escape_special_characters(message.text)}</b>", reply_markup=markup, parse_mode="HTML")
    
    bot.send_message(chat_id=message.chat.id, text="پیام شما ارسال شد\nمنتظر جواب باشید!")
   
    texts[message.from_user.id] = message.text

    bot.delete_state(user_id=message.from_user.id, chat_id=message.chat.id)

@bot.message_handler(state=Support.response)
def answer_text(message): 
    chat_id = chat_ids[-1]

    if chat_id in texts:
        bot.send_message(chat_id=chat_id, text=f"پیام شما:\n<i>{escape_special_characters(texts[chat_id])}</i>\n\nپیام پشتیبانی:\n<b>{escape_special_characters(message.text)}</b>", parse_mode="HTML")
        bot.send_message(chat_id=message.chat.id, text="پیام شما ارسال شد!")
        del texts[chat_id]
        chat_ids.remove(chat_id)
    else:
        bot.send_message(chat_id=message.chat.id, text="مشکلی در ارسال پیام بود بعدا دوباره امتحان کنید.")
    bot.delete_state(user_id=message.from_user.id, chat_id=message.chat.id)

@bot.callback_query_handler(func= lambda call: True)
def admin_answer(call):
    bot.send_message(chat_id=call.message.chat.id, text=f"جواب شما به: <code>{call.data}</code>:", parse_mode="HTML")
    chat_ids.append(int(call.data))
    bot.set_state(user_id=call.from_user.id, state=Support.response, chat_id=call.message.chat.id)


if __name__ == '__main__':
    bot.add_custom_filter(custom_filters.StateFilter(bot))
    bot.infinity_polling()
