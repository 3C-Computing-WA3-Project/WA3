print("Program starting...")
print("Please be patient, it takes around 20 seconds to initialise this program for the first time.")
%pip install beanie bcrypt traitlets --q

import nest_asyncio
import asyncio
import inspect

loop = asyncio.get_event_loop()
nest_asyncio.apply()

from traitlets import HasTraits, dlink, Unicode, Instance
import types

from abc import ABC, abstractmethod

import ipywidgets as widgets
from IPython.display import clear_output, display

import time
from google.colab import userdata

from pydantic import BaseModel, Field, model_validator
from typing import Optional
from beanie import init_beanie, Document, Indexed
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from datetime import datetime

import random

import bcrypt

############################## Data Models ##############################

class UserModel(Document):
    name: str
    username: Indexed(str, unique=True)
    hashedPassword: str

    class Settings:
        collection = "UserModel"

class QuestionModel(Document):
    question: Indexed(str, unique=True)
    solution: str
    answer: str
    title: str
    class Settings:
        collection = "QuestionsBank"

class AttemptModel(Document):
    userId: str
    questionId: str
    timeStamp: datetime = Field(default_factory=datetime.now)
    isCorrect: bool = False
    '''
    Every submission counts just before it is answered correctly.
    The default of `timesOfAnswer` is one as the model will only be inserted if the user submit the answer.
    '''
    timesOfAnswering: int = 1
    class Settings:
        collection = "AttemptModel"

class Quiz(BaseModel):
    question: str
    answer: str
    solution: str
############################## End of Data Models ##############################

############################## Nerfed MVC framework ##############################

class ViewBase():
    def __init_subclass__(cls):
        '''
        This allows the base class to access all static UIs in the subclass during runtime, without using metaclass.
        '''
        cls.widgetsattr = {}
        cls.renderlist = []
        for name, val in vars(cls).items():
            if name.startswith("__") or name == "widgetsattr" or name == "renderlist":
                continue

            if isinstance(val, widgets.Widget):
                '''
                This adds simple UI. For example: helloworld = ipwidgets.widgets.HTML("Hello World")
                '''
                cls.widgetsattr[name] = val
                if not getattr(val, "isIgnored", False):
                    cls.renderlist.append(val)

            elif isinstance(val, list) and all(isinstance(w, widgets.Widget) for w in val):
                '''
                This adds a list of simple UIs.
                '''
                for index, widget_val in enumerate(val):
                    cls.widgetsattr[f"{name}_index_{index}"] = widget_val
                    cls.renderlist.append(widget_val)

            elif isinstance(val, types.GeneratorType):
                '''
                This adds a list of simple UIs from a generator such as from the `yield` keyword.
                '''
                the_list = list(val)
                if all(isinstance(w, widgets.Widget) for w in the_list):
                    for index, widget_val in enumerate(the_list):
                        cls.widgetsattr[f"{name}_index_{index}"] = widget_val
                        cls.renderlist.append(widget_val)

    def __init__(self, appstate):
        self.appstate = appstate
        self.widgets_attr_dict = self.__class__.widgetsattr.copy()

        for name, widget in self.widgets_attr_dict.items():
            setattr(self, name, widget)

        self.widgets = self.__class__.renderlist.copy()
        self.link()

    def to_render(self) -> widgets.VBox:
        return widgets.VBox(
                self.widgets,
                layout = widgets.Layout(
                    max_width ="100%",
                    align_items="center"
                    )
                )

    def link(self):
        '''
        This allow dynamically modify or add widgets.
        '''
        pass

    def binder(self, widget, nameoftrait, transform=None):
        '''
        A helper method that explicitly link the app state trait to the expected widget.
        '''
        if isinstance(widget, widgets.Widget) and hasattr(self.appstate, nameoftrait):
            if transform:
                dlink((self.appstate, nameoftrait), (widget, "value"), transform=transform)
            else:
                dlink((self.appstate, nameoftrait), (widget, "value"))
        else:
            raise TypeError(f"Expected Widget type and valid trait name. Got widget: {type(widget)}, name of trait: {nameoftrait}")

class ControllerBase(ABC):
    @abstractmethod
    def __init__(self, appstate, router):
        self.appstate = appstate
        self.router = router

        self._obj_view = getattr(self, "_obj_view", None)
        if not isinstance(self._obj_view, type):
            raise AttributeError("No view _obj_view detected.")

        if not issubclass(self._obj_view, ViewBase):
            raise TypeError(f"Invalid _obj_view attr. Required ViewBase subclass, got {type(self._obj_view)}")

    def binding(self):
        for name, widget in self.view.widgets_attr_dict.items():
            func = None

            if isinstance(widget, widgets.Button):
                func = getattr(self, f"on_{name}")
            else:
                continue

            widget._click_handlers.callbacks.clear()
            if inspect.iscoroutinefunction(func):
                async def wrapper(_, f=func, w=widget):
                    w.disabled = True
                    await f(_)
                    w.disabled = False

                widget.on_click(lambda _: loop.run_until_complete(wrapper(_)))

            else:
                def wrapper(_, f=func, w=widget):
                    w.disabled = True
                    f(_)
                    w.disabled = False
                widget.on_click(wrapper)

    def show(self):
        self.view = self._obj_view(self.appstate)
        self.binding()
        self.router.container.clear_output()
        with self.router.container:
            display(self.view.to_render())

class AppState(HasTraits):
    '''
    Dynamic app data
    '''
    userId = Unicode()
    name = Unicode()

class Router:
    def __init__(self):
        self.appstate = AppState()
        self.controllers = dict()
        self.container = widgets.Output()
        display(self.container)

    def register_one(self, controller: ControllerBase):
        '''
        As `AppState` class is an instance, I cannot pre-define the value in the ControllerBase and ViewBase class,
        so that I have to inject the AppState instance during runtime.
        '''
        if not issubclass(controller, ControllerBase):
            raise TypeError(f"Expected ControllerBase type got {type(controller)}")

        self.controllers[controller.__name__] = controller(self.appstate, self)

    def go(self, controller: ControllerBase):
        '''
        `controller` argument takes ControllerBase object.
        This function go the given `controller`
        '''
        self.controllers[controller.__name__].show()

############################## End of Nerfed MVC framework ##############################

class QuizHelper:
    '''
    the 'question' argument must be a calleable function which returns the quiz class
    '''
    def __init__(self, *, appstate, quiz, title:str, numberOfQuestions = 5):
        self.quiz=quiz
        self.title=title
        self.numberOfQuestions=numberOfQuestions
        self.appstate=appstate

    async def build_ui(self):
        question_list = []
        for x in range(self.numberOfQuestions):
            quiz_obj = None
            questionid = ""
            while(True):
                try:
                    quiz_obj = self.quiz()
                    question_model = QuestionModel(
                        question = quiz_obj.question,
                        solution = quiz_obj.solution,
                        answer = quiz_obj.answer,
                        title = self.title
                        )
                    await question_model.insert()
                    questionid = str(question_model.id)
                    break
                except DuplicateKeyError:
                    pass

            ask = widgets.HTML(f"<strong>{quiz_obj.question}</strong>")

            answer = widgets.Text(
                placeholder="Enter your answer",
                description=f"Question {x+1}:",
                disabled=False
            )

            correct = widgets.HTML("<strong style='color:green'>Correct!</strong>", layout=widgets.Layout(
                display="none"
            ))

            incorrect = widgets.HTML("<strong style='color:red'>Incorrect! Try again or show the solution</strong>", layout=widgets.Layout(
                display="none"
            ))


            solution = widgets.HTML(f"<strong>{quiz_obj.solution}</strong>", layout=widgets.Layout(
                display="none"
            ))

            show_solution_btn = widgets.Button(
                description="Show Solution",
                disabled=False,
                button_style="warning",
                layout=widgets.Layout(
                    display="none"
                )
            )
            def show_solution(_, w_solution = solution):
                w_solution.layout.display=""
            show_solution_btn.on_click(show_solution)

            submit_btn = widgets.Button(
                description="Submit",
                disabled=False,
                button_style="info"
            )
            # Creation of variable after instantiation
            submit_btn.isSubmitted = False
            submit_btn.attempt_model = AttemptModel(
                userId = self.appstate.userId,
                questionId = questionid,
                isCorrect = False
            )
            async def submit(
                *,
                event,
                w_quiz_obj,
                w_answer,
                w_correct,
                w_incorrect,
                w_show_solution_btn
            ):
                event.disabled = True
                isCorrect = w_answer.value.strip() == w_quiz_obj.answer
                w_correct.layout.display="none"
                w_incorrect.layout.display="none"
                w_show_solution_btn.layout.display="none"
                if isCorrect:
                    w_correct.layout.display=""
                else:
                    w_incorrect.layout.display=""
                    w_show_solution_btn.layout.display=""

                if not event.isSubmitted:
                    event.attempt_model.isCorrect = isCorrect
                    await event.attempt_model.insert()
                else:
                    prev = event.attempt_model.isCorrect
                    event.attempt_model.isCorrect = isCorrect or event.attempt_model.isCorrect
                    event.attempt_model.timesOfAnswering += 1 if not prev else 0
                    await event.attempt_model.save()
                event.isSubmitted = True
                event.disabled = False

            submit_btn.on_click(
                lambda event,
                w_quiz_obj=quiz_obj,
                w_answer=answer,
                w_incorrect=incorrect,
                w_correct=correct,
                w_show_solution_btn=show_solution_btn:
            loop.run_until_complete(
                submit(
                    event=event,
                    w_quiz_obj=w_quiz_obj,
                    w_answer=w_answer,
                    w_correct=w_correct,
                    w_incorrect=w_incorrect,
                    w_show_solution_btn=w_show_solution_btn,
                    )
                )
            )

            btn_container = widgets.HBox([submit_btn, show_solution_btn])
            quiz_container = widgets.VBox([ask, answer, solution, btn_container, correct, incorrect], layout=widgets.Layout(
                border="3px solid",
                padding="1em",
                margin="1em 0"
            ))
            question_list.append(quiz_container)

        return question_list # To make my life easier, I omitted yield keyword

def make_title(text, _layout=None):
    return widgets.HTML(f"<h1 style='color: teal'>{text}</h1>",layout=_layout if _layout is not None else widgets.Layout(
        text_align="center"
    ))

class MainMenuView(ViewBase):
    title = make_title("Main Menu")

    btn_login = widgets.Button(
            description="Login",
            disabled=False,
            button_style="primary"
    )

    btn_register = widgets.Button(
            description="Register",
            disabled=False,
            button_style="primary"
    )

    btn_exit = widgets.Button(
            description="Exit",
            disabled=False,
            button_style="danger"
    )

class MainMenuController(ControllerBase):
    def __init__(self, appstate, router):
        self._obj_view = MainMenuView
        super().__init__(appstate, router)

    def on_btn_login(self, event):
        self.router.go(LoginController)

    def on_btn_register(self, event):
        self.router.go(RegisterController)

    def on_btn_exit(self, event):
        pass

class RegisterView(ViewBase):
    title = make_title("Register Page")

    name = widgets.Text(
            placeholder='Enter your name',
            description='Name: ',
            disabled=False,
            style={'description_width': '140px'}
    )

    username = widgets.Text(
        placeholder='Enter your username',
        description='Username: ',
        disabled=False,
        style={'description_width': '140px'}
    )

    password = widgets.Password(
        description='Password:',
        disabled=False,
        style={'description_width': '140px'}
    )

    confirmed_password = widgets.Password(
        description='Confirmed Password:',
        disabled=False,
        style={'description_width': '140px'}
    )

    error_text_password_not_match = widgets.HTML(
        value="<strong style='color:red'>Password does not match!</strong>",
        layout=widgets.Layout(display="none")
    )

    error_text_password_length = widgets.HTML(
        value="<strong style='color:red'>Password is too short!</strong>",
        layout=widgets.Layout(display="none")
    )

    error_text_username = widgets.HTML(
        value="<strong style='color:red'>This username is chosen!</strong>",
        layout=widgets.Layout(display="none")
    )

    btn_exit = widgets.Button(
        description="Exit",
        disabled=False,
        button_style="danger"
    )
    btn_exit.isIgnored = True

    btn_register = widgets.Button(
        description="Register",
        disabled=False,
        button_style="primary"
    )
    btn_register.isIgnored = True

    box = widgets.HBox([btn_exit, btn_register], layout=widgets.Layout(
        justify_content="space-between",
        grid_gap="1.5em"
    ))

    succeeded = widgets.HTML(
        value="<strong style='color:green'>Successfully registered an account! Click 'exit' to return back.</strong>",
        layout=widgets.Layout(display="none")
    )

class RegisterController(ControllerBase):
    def __init__(self, appstate, router):
        self._obj_view = RegisterView
        super().__init__(appstate, router)

    def on_btn_exit(self, event):
        self.router.go(MainMenuController)

    async def on_btn_register(self, event):
        try:
            self.view.error_text_password_not_match.layout.display = "none"
            self.view.error_text_password_length.layout.display = "none"
            self.view.error_text_username.layout.display = "none"
            self.view.succeeded.layout.display = "none"

            pwd = self.view.password.value
            conf = self.view.confirmed_password.value

            # validation
            if pwd != conf:
                self.view.error_text_password_not_match.layout.display = ""
                return

            if len(pwd) < 8:
                self.view.error_text_password_length.layout.display = ""
                return

            salt = bcrypt.gensalt()
            hashed = bcrypt.hashpw(pwd.encode("utf-8"), salt)

            data = UserModel(
                name=self.view.name.value,
                username=self.view.username.value,
                hashedPassword=hashed
            )
            self.data = data
            await data.insert()
            self.view.succeeded.layout.display = ""

        except DuplicateKeyError:
            self.view.error_text_username.layout.display = ""

class LoginView(ViewBase):
    title = make_title("Login Page")

    username = widgets.Text(
        placeholder='Enter your username',
        description='Username: ',
        disabled=False,
    )

    password = widgets.Password(
        description='Password:',
        disabled=False,
    )

    error_text = widgets.HTML(
        value="<strong style='color:red'>Invalid username or/and password</strong>",
        layout=widgets.Layout(display="none")
    )

    btn_exit = widgets.Button(
        description="Exit",
        disabled=False,
        button_style="danger"
    )
    btn_exit.isIgnored = True

    btn_login = widgets.Button(
        description="Login",
        disabled=False,
        button_style="primary"
    )
    btn_login.isIgnored = True

    box = widgets.HBox([btn_exit, btn_login], layout=widgets.Layout(
        justify_content="space-between",
        grid_gap="1.5em"
    ))

class LoginController(ControllerBase):
    def __init__(self, appstate, router):
        self._obj_view = LoginView
        super().__init__(appstate, router)

    def on_btn_exit(self, event):
        self.router.go(MainMenuController)

    async def on_btn_login(self, event):
        self.view.error_text.layout.display = "none"

        username = self.view.username.value
        pwd = self.view.password.value

        result = await UserModel.find_one(UserModel.username == username)
        hash = ""
        fakeHash = bcrypt.hashpw(b"invalid", bcrypt.gensalt()) # the register function restricted the lenght of password to be at least eight-character long. Suggested by https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html#authentication-responses

        if result is not None:
            hash = result.hashedPassword.encode("utf-8")
        else:
            hash = fakeHash

        isAuth = bcrypt.checkpw(pwd.encode("utf-8"), hash) and result is not None
        if isAuth:
            self.appstate.name = result.name
            self.appstate.userId = str(result.id)
            self.router.go(DashboardController)
        else:
            self.view.error_text.layout.display = ""

class DashboardView(ViewBase):

    title = make_title("Dashboard")
    title.isIgnored = True

    btn_sign_out = widgets.Button(
        description="Sign Out",
        disabled=False,
        button_style="danger"
    )
    btn_sign_out.isIgnored = True

    header = widgets.HBox([title, btn_sign_out], layout=widgets.Layout(
        justify_content="space-between",
        align_items="center",
        width="auto",
    ))
    header.isIgnored = True

    welcome_msg = widgets.HTML()
    welcome_msg.isIgnored = True

    options = widgets.Select(
        options=["Quadratic Equation"],
        description="Topics: ",
        disabled=False,
        style={
            'description_width': 'initial'
        }
    )
    options.isIgnored = True

    btn_proceed = widgets.Button(
        description="Proceed",
        disabled=False,
        button_style="info"
    )
    btn_proceed.isIgnored = True

    container_options = widgets.VBox([options, btn_proceed], layout=widgets.Layout(
        align_items="center",
        width="30em"
    ))
    container_options.isIgnored = True

    center = widgets.HBox([welcome_msg, container_options], layout=widgets.Layout(
        margin="1px 0",
        width="auto",
        display="grid"
    ))
    center.isIgnored = True

    Layout = widgets.AppLayout(
        header=header,
        left_sidebar=None,
        center=center,
        right_sidebar=None,
        footer=None,
        layout=widgets.Layout(
            width="100%",
            padding="1em",
        )
    )

    def link(self):
        self.binder(self.welcome_msg, AppState.name.name, lambda x: f"<strong>Welcome back! How are you, <span style='color:green'>{x}</span>?<strong/>")

class DashboardController(ControllerBase):
    def __init__(self, appstate, router):
        self._obj_view = DashboardView
        super().__init__(appstate, router)

    def on_btn_sign_out(self, event):
        self.router.go(MainMenuController)

    def on_btn_proceed(self, event):
        match self.view.options.value:
            case "Quadratic Equation":
                self.router.go(QuadraticEquationsController)

class QuadraticEquationsView(ViewBase):
    title = make_title("Quadratic Equation Quiz")

    instruction = widgets.HTML("<strong>Solve for x for each question</strong>")

    exit_btn = widgets.Button(
        description="Exit",
        disabled=False,
        button_style="danger"
    )

    def link(self):
        new_widgets = loop.run_until_complete(
            QuizHelper(
                appstate=self.appstate,
                quiz=self.quiz_generator,
                title="Quadratic Equation"
            ).build_ui()
        )
        self.widgets.extend(new_widgets)

    def quiz_generator(self):
        return Quiz(
            question = "Sample Quadratic Equation Question"+ str(random.randint(0, 1000)),
            solution = "Sample Quadratic Equation Solution",
            answer = "answer"
        )

class QuadraticEquationsController(ControllerBase):
    def __init__(self, appstate, router):
        self._obj_view = QuadraticEquationsView
        super().__init__(appstate, router)

    def on_exit_btn(self, event):
        self.router.go(DashboardController)

############################## ENTRY POINT ##############################
print("Trying to connect to the database...")

# Create a new client and connect to the server
client = AsyncIOMotorClient(userdata.get("MongoDBAtlasConnectionString")) # The connection string will be revoked after WA3 is graded

await init_beanie(database=client["WA3"], document_models=[UserModel, QuestionModel, AttemptModel])
await client.admin.command('ping')

print("Connected!")
time.sleep(0.3)

clear_output()

router = Router()
router.register_one(MainMenuController)
router.register_one(RegisterController)
router.register_one(LoginController)
router.register_one(DashboardController)
router.register_one(QuadraticEquationsController)

router.go(MainMenuController)
